import argparse
import importlib.metadata
import json
import logging
import os
import pathlib
import threading
import time
from typing import Annotated, Literal

import anyio

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from rlm_tools_bsl.session import SessionManager
from rlm_tools_bsl.sandbox import Sandbox
from rlm_tools_bsl.llm_bridge import get_llm_query_fn, make_llm_query_batched
from rlm_tools_bsl.format_detector import detect_format
from rlm_tools_bsl.bsl_knowledge import (
    EFFORT_LEVELS,
    RLM_EXECUTE_DESCRIPTION,
    RLM_START_DESCRIPTION,
    get_strategy,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("rlm-tools-bsl")

session_manager = SessionManager(
    max_sessions=int(os.environ.get("RLM_MAX_SESSIONS", "5")),
    timeout_minutes=int(os.environ.get("RLM_SESSION_TIMEOUT", "10")),
)

_sandboxes: dict[str, Sandbox] = {}
_sandboxes_lock = threading.Lock()


from rlm_tools_bsl.helpers import _SKIP_DIRS, _BINARY_EXTENSIONS


def _scan_metadata(path: str) -> dict:
    extensions: dict[str, int] = {}
    total_files = 0
    total_lines = 0
    sampled_lines = 0
    sampled_files = 0
    sample_budget = 500

    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]

        for fname in filenames:
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1] or "(no ext)"
            extensions[ext] = extensions.get(ext, 0) + 1
            total_files += 1

            if ext not in _BINARY_EXTENSIONS:
                try:
                    fpath = os.path.join(dirpath, fname)
                    with open(fpath, encoding="utf-8-sig", errors="replace") as f:
                        file_line_count = sum(1 for _ in f)
                    total_lines += file_line_count

                    if sampled_files < sample_budget:
                        sampled_lines += file_line_count
                        sampled_files += 1
                except OSError:
                    pass

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "sampled_lines": sampled_lines,
        "sampled_files": sampled_files,
        "file_types": dict(sorted(extensions.items(), key=lambda x: -x[1])[:10]),
    }


def _cleanup_expired_resources() -> None:
    expired_session_ids = session_manager.cleanup_expired()
    with _sandboxes_lock:
        for session_id in expired_session_ids:
            _sandboxes.pop(session_id, None)


def _resolve_mapped_drive(path: str) -> str | None:
    """Resolve mapped drive letter to UNC path via Windows registry.

    Services in Session 0 cannot see interactive session drive mappings.
    This reads HKEY_USERS\\<SID>\\Network\\<letter>\\RemotePath instead.
    """
    if os.name != "nt" or len(path) < 2 or path[1] != ":":
        return None
    drive_letter = path[0].upper()
    try:
        import winreg

        i = 0
        while True:
            try:
                sid = winreg.EnumKey(winreg.HKEY_USERS, i)
            except OSError:
                break
            i += 1
            if sid.startswith(".") or sid.endswith("_Classes"):
                continue
            try:
                with winreg.OpenKey(
                    winreg.HKEY_USERS, f"{sid}\\Network\\{drive_letter}"
                ) as key:
                    remote_path, _ = winreg.QueryValueEx(key, "RemotePath")
                    if remote_path:
                        return remote_path + path[2:]
            except OSError:
                continue
    except Exception:
        pass
    return None


def _install_session_llm_tools(session, sandbox: Sandbox) -> bool:
    try:
        base_llm_query = get_llm_query_fn()
        if base_llm_query is None:
            logger.info("llm_query not available (no LLM provider configured)")
            return False
        base_llm_query_batched = make_llm_query_batched(base_llm_query)
        lock = threading.Lock()

        def _reserve_llm_calls(count: int) -> None:
            if count < 1:
                raise ValueError("count must be >= 1")
            with lock:
                if session.llm_calls_used + count > session.max_llm_calls:
                    raise RuntimeError(
                        "LLM call limit exceeded: "
                        f"{session.llm_calls_used} + {count} > {session.max_llm_calls}"
                    )
                session.llm_calls_used += count

        def llm_query(prompt: str, context: str = "") -> str:
            _reserve_llm_calls(1)
            return base_llm_query(prompt, context)

        def llm_query_batched(prompts: list[str], context: str = "") -> list[str]:
            if not prompts:
                return []
            _reserve_llm_calls(len(prompts))
            return base_llm_query_batched(prompts, context)

        sandbox._namespace["llm_query"] = llm_query
        sandbox._namespace["llm_query_batched"] = llm_query_batched
        return True
    except Exception as e:
        logger.warning(f"Could not initialize llm_query: {e}")
        return False


def _rlm_start(
    path: str,
    query: str,
    effort: str = "medium",
    max_output_chars: int = 15_000,
    max_llm_calls: int | None = None,
    max_execute_calls: int | None = None,
    execution_timeout_seconds: int = 45,
    include_metadata: bool = False,
) -> str:
    t0 = time.monotonic()
    logger.info("rlm_start: path=%s effort=%s include_metadata=%s", path, effort, include_metadata)
    _cleanup_expired_resources()

    resolved = str(pathlib.Path(path).resolve())
    if not os.path.isdir(resolved):
        # Try resolving mapped drive via registry (Windows service in Session 0)
        unc_path = _resolve_mapped_drive(path)
        if unc_path:
            resolved = str(pathlib.Path(unc_path).resolve())
        if not os.path.isdir(resolved):
            hint = ""
            if len(path) >= 2 and path[1] == ":" and not os.path.isdir(path[:3]):
                hint = (
                    f" (drive {path[:2]} is not accessible to this process; "
                    "use UNC path like \\\\server\\share\\... instead)"
                )
            return json.dumps(
                {"error": f"Directory not found: {path}{hint}"},
                ensure_ascii=False,
            )

    effort_config = EFFORT_LEVELS.get(effort, EFFORT_LEVELS["medium"])
    if max_llm_calls is None:
        max_llm_calls = effort_config.max_llm_calls
    if max_execute_calls is None:
        max_execute_calls = effort_config.max_execute_calls

    try:
        session_id = session_manager.create(
            path=resolved,
            query=query,
            max_output_chars=max_output_chars,
            max_llm_calls=max_llm_calls,
            max_execute_calls=max_execute_calls,
        )
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    session = session_manager.get(session_id)
    if not session:
        return json.dumps({"error": f"Failed to create session for path: {path}"}, ensure_ascii=False)

    logger.info("rlm_start: session=%s created for path=%s", session_id, resolved)

    try:
        metadata = _scan_metadata(resolved) if include_metadata else {}

        format_info = detect_format(resolved)
        logger.info(
            "rlm_start: session=%s format=%s bsl_files=%d",
            session_id, format_info.format_label, format_info.bsl_file_count,
        )
        strategy = get_strategy(effort, format_info)

        sandbox = Sandbox(
            base_path=resolved,
            max_output_chars=max_output_chars,
            execution_timeout_seconds=execution_timeout_seconds,
            format_info=format_info,
        )
        has_llm_tools = _install_session_llm_tools(session, sandbox)
        logger.info("rlm_start: session=%s sandbox ready, llm_tools=%s", session_id, has_llm_tools)

        with _sandboxes_lock:
            _sandboxes[session_id] = sandbox
    except Exception as e:
        logger.error("rlm_start: session=%s failed: %s", session_id, e, exc_info=True)
        session_manager.end(session_id)
        return json.dumps(
            {"error": f"Session init failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    available_functions = [
        "help(task='') -> str  # get recipe for your task, e.g. help('find exports') or help('граф вызовов')",
        "find_module(name) -> list[dict] keys: path, category, object_name, module_type",
        "find_by_type(category, name='') -> list[dict]. Categories: CommonModules, Documents, Catalogs, InformationRegisters, AccumulationRegisters, Reports, DataProcessors",
        "find_exports(path) -> list[dict] keys: name, line, is_export, type, params",
        "extract_procedures(path) -> list[dict] keys: name, type, line, end_line, is_export, params",
        "find_callers_context(proc_name, module_hint='', offset=0, limit=50) -> {callers: [...], _meta: {total_files, has_more}}",
        "find_callers(proc_name, module_hint='', max_files=20) -> list[dict] keys: file, line, text",
        "safe_grep(pattern, name_hint='', max_files=20) -> list[dict] keys: file, line, text",
        "read_procedure(path, proc_name) -> str|None",
        "parse_object_xml(path) -> dict  # parse 1C metadata XML: attributes, tabular sections, dimensions, resources",
        "analyze_subsystem(name) -> dict  # find subsystem, parse composition, classify custom/standard objects",
        "find_custom_modifications(object_name, custom_prefixes=['лтх',...]) -> dict  # find custom code in object modules",
        "analyze_object(name) -> dict  # full object profile: XML metadata + all modules + procedures + exports",
        "read_file(path) -> str",
        "read_files(paths) -> dict[path, content]",
        "grep(pattern, path='.') -> list[dict] keys: file, line, text",
        "grep_summary(pattern, path='.') -> compact grouped string",
        "grep_read(pattern, path='.', max_files=10, context_lines=0) -> {matches, files, summary}",
        "glob_files(pattern) -> list[str]",
        "tree(path='.', max_depth=3) -> str",
        "find_files(name) -> list[str]",
    ]
    if has_llm_tools:
        available_functions.extend([
            "llm_query(prompt, context='')",
            "llm_query_batched(prompts, context='')",
        ])

    response: dict = {
        "session_id": session_id,
        "config_format": format_info.format_label,
        "metadata": metadata,
        "limits": {
            "max_llm_calls": session.max_llm_calls,
            "max_execute_calls": session.max_execute_calls,
            "execution_timeout_seconds": execution_timeout_seconds,
        },
        "available_functions": available_functions,
        "strategy": strategy,
    }
    logger.info("rlm_start: session=%s completed in %.2fs", session_id, time.monotonic() - t0)
    return json.dumps(response, ensure_ascii=False)


def _rlm_execute(
    session_id: str,
    code: str,
    detail_level: Literal["compact", "usage", "full"] = "compact",
    max_new_variables: int = 20,
) -> str:
    t0 = time.monotonic()
    logger.info("rlm_execute: session=%s code_len=%d", session_id, len(code))
    _cleanup_expired_resources()
    session = session_manager.get(session_id)
    if not session:
        return json.dumps({"error": f"Session '{session_id}' not found or expired"}, ensure_ascii=False)

    with _sandboxes_lock:
        sandbox = _sandboxes.get(session_id)
    if not sandbox:
        return json.dumps({"error": f"Sandbox not found for session '{session_id}'"}, ensure_ascii=False)

    if session.execute_calls >= session.max_execute_calls:
        return json.dumps({
            "error": (
                "Execution call limit exceeded: "
                f"{session.execute_calls} >= {session.max_execute_calls}"
            )
        }, ensure_ascii=False)

    session.execute_calls += 1
    result = sandbox.execute(code)

    logger.info(
        "rlm_execute: session=%s call=%d/%d error=%s elapsed=%.2fs",
        session_id, session.execute_calls, session.max_execute_calls,
        bool(result.error), time.monotonic() - t0,
    )

    response: dict = {
        "stdout": result.stdout,
        "error": result.error,
    }

    if detail_level in {"usage", "full"}:
        response["usage"] = {
            "execute_calls_used": session.execute_calls,
            "execute_calls_remaining": session.max_execute_calls - session.execute_calls,
            "llm_calls_used": session.llm_calls_used,
        }

    if detail_level == "full":
        current_vars = set(result.variables)
        previous_vars = getattr(session, "_last_reported_vars", set())
        excluded_vars = {
            "read_file", "read_files",
            "grep", "grep_summary", "grep_read",
            "glob_files", "tree", "find_files",
            "find_module", "find_by_type",
            "extract_procedures", "find_exports",
            "safe_grep", "read_procedure", "find_callers", "parse_object_xml",
            "llm_query", "llm_query_batched",
        }
        new_vars = sorted(
            v for v in (current_vars - previous_vars)
            if v not in excluded_vars
        )
        session._last_reported_vars = current_vars

        response["variables"] = sorted(v for v in current_vars if v not in excluded_vars)
        response["total_variables"] = len(response["variables"])
        response["new_variables"] = new_vars[:max_new_variables]
        if len(new_vars) > max_new_variables:
            response["new_variables_truncated_count"] = len(new_vars) - max_new_variables

    return json.dumps(response, ensure_ascii=False)


def _rlm_end(session_id: str) -> str:
    logger.info("rlm_end: session=%s", session_id)
    session_manager.end(session_id)
    with _sandboxes_lock:
        _sandboxes.pop(session_id, None)
    return json.dumps({"success": True}, ensure_ascii=False)


@mcp.tool()
async def rlm_start(
    path: Annotated[str, Field(description="Absolute path to the 1C BSL codebase directory")],
    query: Annotated[str, Field(description="What you want to find or analyze in the BSL codebase")],
    effort: Annotated[str, Field(description="Analysis depth: low (quick lookup), medium (standard), high (deep trace), max (exhaustive)")] = "medium",
    max_output_chars: Annotated[int, Field(description="Max characters per execute output", ge=100, le=100_000)] = 15_000,
    max_llm_calls: Annotated[int | None, Field(description="Override max llm_query calls (default from effort level)")] = None,
    max_execute_calls: Annotated[int | None, Field(description="Override max rlm_execute calls (default from effort level)")] = None,
    execution_timeout_seconds: Annotated[int, Field(description="Per-rlm_execute timeout in seconds", ge=1, le=300)] = 45,
    include_metadata: Annotated[bool, Field(description="Scan directory and include file counts/types in response (slow on large configs, disabled by default)")] = False,
) -> str:
    """Start a BSL code exploration session on a 1C codebase. Returns JSON with session_id. Then call rlm_execute(session_id, code) where code is Python that calls helper functions and uses print() to output results. IMPORTANT: For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first."""
    return await anyio.to_thread.run_sync(
        lambda: _rlm_start(
            path=path,
            query=query,
            effort=effort,
            max_output_chars=max_output_chars,
            max_llm_calls=max_llm_calls,
            max_execute_calls=max_execute_calls,
            execution_timeout_seconds=execution_timeout_seconds,
            include_metadata=include_metadata,
        )
    )


@mcp.tool()
async def rlm_execute(
    session_id: Annotated[str, Field(description="Session ID from rlm_start")],
    code: Annotated[str, Field(description=(
        "Python code to execute. IMPORTANT: Batch multiple related operations into each call. "
        "A good call does: grep -> read top matches -> extract patterns -> print summary. "
        "A bad call does just one grep or one read_file. Variables persist between calls."
    ))],
    detail_level: Annotated[Literal["compact", "usage", "full"], Field(
        description="Response payload level: compact=stdout+error, usage=add usage metrics, full=add variable details"
    )] = "compact",
    max_new_variables: Annotated[int, Field(
        description="When detail_level=full, cap returned new_variables list to this size",
        ge=1,
        le=200,
    )] = 20,
) -> str:
    """Execute Python code in the BSL sandbox. The 'code' parameter is Python code. Call helper functions and use print() to see results. Variables persist between calls. Example: code="modules = find_module('MyModule')\\nfor m in modules:\\n    print(m['path'])". BSL helpers: help, find_module, find_by_type, extract_procedures, find_exports, safe_grep, read_procedure, find_callers, find_callers_context, parse_object_xml. Standard: read_file, read_files, grep, grep_summary, grep_read, glob_files, tree. CRITICAL: grep on path='.' ALWAYS times out on large 1C configs. Use find_module() first."""
    return await anyio.to_thread.run_sync(
        lambda: _rlm_execute(session_id, code, detail_level, max_new_variables)
    )


@mcp.tool()
async def rlm_end(
    session_id: Annotated[str, Field(description="Session ID to end")],
) -> str:
    """End an RLM exploration session and free resources."""
    return await anyio.to_thread.run_sync(lambda: _rlm_end(session_id))


def _setup_file_logging():
    """Add rotating file handler for HTTP transport mode."""
    from logging.handlers import RotatingFileHandler

    # Use RLM_CONFIG_FILE-derived path if set (Windows service / Session 0)
    config_override = os.environ.get("RLM_CONFIG_FILE")
    if config_override:
        log_dir = pathlib.Path(config_override).parent / "logs"
    else:
        log_dir = pathlib.Path.home() / ".config" / "rlm-tools-bsl" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)
    logger.info("File logging enabled: %s", log_path)


def main():
    # Load .env file (next to the executable, cwd, or project root)
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True), override=True)
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="rlm-tools-bsl MCP server")
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('rlm-tools-bsl')}",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("RLM_TRANSPORT", "stdio"),
        help="Transport protocol (env: RLM_TRANSPORT, default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("RLM_HOST", "127.0.0.1"),
        help="Bind host for HTTP transport (env: RLM_HOST, default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RLM_PORT", "9000")),
        help="Bind port for HTTP transport (env: RLM_PORT, default: 9000)",
    )

    subparsers = parser.add_subparsers(dest="command")
    service_parser = subparsers.add_parser("service", help="Manage system service (Windows SC / Linux systemd)")
    service_sub = service_parser.add_subparsers(dest="service_action")

    install_p = service_sub.add_parser("install", help="Install and enable the service")
    install_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    install_p.add_argument("--port", type=int, default=9000, help="Bind port (default: 9000)")
    install_p.add_argument("--env", default=None, metavar="PATH", help="Path to .env file")

    for _action in ("start", "stop", "status", "uninstall"):
        service_sub.add_parser(_action)

    args = parser.parse_args()

    if args.command == "service":
        from rlm_tools_bsl.service import handle_service_command
        handle_service_command(args)
        return

    if args.transport != "stdio":
        _setup_file_logging()
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)
