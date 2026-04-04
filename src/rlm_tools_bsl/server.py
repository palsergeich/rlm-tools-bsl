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
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from rlm_tools_bsl.session import SessionManager, build_session_manager_from_env
from rlm_tools_bsl.sandbox import Sandbox
from rlm_tools_bsl.llm_bridge import get_llm_query_fn, make_llm_query_batched, warmup_openai_import
from rlm_tools_bsl.format_detector import FormatInfo, SourceFormat, detect_format
from rlm_tools_bsl.extension_detector import (
    ConfigRole,
    detect_extension_context,
    find_extension_overrides,
)
from rlm_tools_bsl.bsl_knowledge import (
    EFFORT_LEVELS,
    get_strategy,
)
from rlm_tools_bsl.bsl_index import (
    BUILDER_VERSION,
    IndexReader,
    IndexStatus,
    check_index_usable,
    get_index_db_path,
)
from rlm_tools_bsl.sandbox import HelperCall

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("rlm-tools-bsl", stateless_http=True)

session_manager = SessionManager()  # defaults for tests/import

_sandboxes: dict[str, Sandbox] = {}
_idx_readers: dict[str, IndexReader] = {}
_sandboxes_lock = threading.Lock()


@mcp.custom_route("/health", methods=["GET"])
async def _health_endpoint(request):  # type: ignore[no-untyped-def]
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})


from rlm_tools_bsl.helpers import _SKIP_DIRS, _BINARY_EXTENSIONS

_MAX_OVERRIDES_IN_RESPONSE = 100


def _auto_scan_overrides(ext_context) -> dict[str, list[dict]]:
    """Auto-scan extension overrides during rlm_start.

    Returns dict mapping extension path -> list of override dicts.
    If current path is an extension, scans itself under key "self".
    If main config with nearby extensions, scans each extension.
    """

    result: dict[str, list[dict]] = {}
    current = ext_context.current

    try:
        if current.role == ConfigRole.EXTENSION:
            overrides = find_extension_overrides(current.path)
            result["self"] = overrides[:_MAX_OVERRIDES_IN_RESPONSE]

        elif current.role == ConfigRole.MAIN and ext_context.nearby_extensions:
            for ext in ext_context.nearby_extensions:
                overrides = find_extension_overrides(ext.path)
                result[ext.path] = overrides[:_MAX_OVERRIDES_IN_RESPONSE]
    except Exception:
        pass  # non-critical, don't fail rlm_start

    return result


def _scan_metadata(path: str) -> dict:
    extensions: dict[str, int] = {}
    total_files = 0
    total_lines = 0
    sampled_lines = 0
    sampled_files = 0
    sample_budget = 500

    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]

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
            reader = _idx_readers.pop(session_id, None)
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass


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
                with winreg.OpenKey(winreg.HKEY_USERS, f"{sid}\\Network\\{drive_letter}") as key:
                    remote_path, _ = winreg.QueryValueEx(key, "RemotePath")
                    if remote_path:
                        return remote_path + path[2:]
            except OSError:
                continue
    except Exception:
        pass
    return None


def _resolve_path_map(path: str) -> str:
    """Translate host filesystem path to container path using RLM_PATH_MAP.

    RLM_PATH_MAP format: "host_prefix:container_prefix"
    Example: "D:/MyDEV/Repo:/repos"

    Handles Windows backslashes and case-insensitive prefix matching.
    Returns the original path if no mapping matches.
    """
    mapping = os.environ.get("RLM_PATH_MAP", "")
    if not mapping:
        return path
    # Split on last ":" to handle Windows drive letters (e.g. "D:/foo:/repos")
    # Find the separator: scan from the end for ":" that is not at position 1 (drive letter)
    sep_idx = mapping.rfind(":")
    if sep_idx <= 0:
        return path
    # Handle edge case: "D:/foo:/bar" — rfind gives the right split
    host_prefix = mapping[:sep_idx]
    container_prefix = mapping[sep_idx + 1 :]
    if not host_prefix or not container_prefix:
        return path

    # Normalize separators for comparison
    path_normalized = path.replace("\\", "/")
    host_normalized = host_prefix.replace("\\", "/").rstrip("/")

    # Case-insensitive prefix match (Windows paths) with boundary check
    if path_normalized.lower().startswith(host_normalized.lower()):
        remainder = path_normalized[len(host_normalized) :]
        if remainder and not remainder.startswith("/"):
            return path  # not a true directory boundary match
        return container_prefix.rstrip("/") + remainder

    return path


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
                        f"LLM call limit exceeded: {session.llm_calls_used} + {count} > {session.max_llm_calls}"
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
    path: str | None,
    query: str,
    effort: str = "medium",
    max_output_chars: int = 15_000,
    max_llm_calls: int | None = None,
    max_execute_calls: int | None = None,
    execution_timeout_seconds: int = 45,
    include_metadata: bool = False,
    project: str | None = None,
) -> str:
    t0 = time.monotonic()
    _cleanup_expired_resources()

    # --- Resolve project name to path ---
    project_hint: str | None = None

    if path is None and project is None:
        return json.dumps(
            {"error": "Either 'path' or 'project' must be provided"},
            ensure_ascii=False,
        )

    if path is None:
        from rlm_tools_bsl.projects import RegistryCorruptedError, get_registry

        try:
            reg = get_registry()
            matches, method = reg.resolve(project)  # type: ignore[arg-type]
        except RegistryCorruptedError as exc:
            return json.dumps(
                {"error": f"Registry file is corrupted: {exc}. Run rlm_projects(action='list') after fixing the file."},
                ensure_ascii=False,
            )
        if not matches:
            all_projects = reg.list_projects()
            available = [{"name": p["name"], "description": p.get("description", "")} for p in all_projects]
            return json.dumps(
                {
                    "error": f"Project not found: {project}",
                    "available_projects": available,
                },
                ensure_ascii=False,
            )
        if len(matches) > 1:
            ambiguous = [{"name": p["name"], "description": p.get("description", "")} for p in matches]
            return json.dumps(
                {
                    "error": f"Ambiguous project name: {project}",
                    "matches": ambiguous,
                },
                ensure_ascii=False,
            )
        # Single match
        if method == "fuzzy":
            return json.dumps(
                {"error": f"Did you mean '{matches[0]['name']}'?"},
                ensure_ascii=False,
            )
        # exact or substring -- OK
        path = matches[0]["path"]
    else:
        # path is provided -- check if registered, hint if not
        path = _resolve_path_map(path)
        from rlm_tools_bsl.projects import get_registry

        try:
            reg = get_registry()
            if not reg.is_path_registered(path):
                project_hint = (
                    "This path is not in the project registry. "
                    "Register it with rlm_projects(action='add', name='...', path='...') "
                    "to use rlm_start(project='name') next time."
                )
        except Exception:
            pass  # non-critical

    logger.info("rlm_start: path=%s effort=%s include_metadata=%s", path, effort, include_metadata)

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

        # --- Try loading index FIRST (to enable fast-path startup) ---
        t_step = time.monotonic()
        idx_reader = None
        idx_warnings: list[str] = []
        idx_stats: dict | None = None
        idx_status = None
        try:
            db_path = get_index_db_path(resolved)
            if db_path.exists():
                idx_status = check_index_usable(db_path, resolved)
                logger.info(
                    "rlm_start: session=%s index status=%s db=%s",
                    session_id,
                    idx_status.value,
                    db_path,
                )

                if idx_status in (IndexStatus.FRESH, IndexStatus.STALE_AGE, IndexStatus.STALE_CONTENT):
                    idx_reader = IndexReader(db_path)
                    idx_stats = idx_reader.get_statistics()
                    if idx_status == IndexStatus.STALE_AGE:
                        built_at = idx_stats.get("built_at")
                        age_days = int((time.time() - float(built_at)) / 86400) if built_at else "?"
                        idx_warnings.append(
                            f"Index is {age_days} days old — verify critical findings with live read_file()"
                        )
                    elif idx_status == IndexStatus.STALE_CONTENT:
                        idx_warnings.append(
                            "Index content may be outdated — run 'rlm-bsl-index index update' to refresh"
                        )
                    # Check index builder version
                    idx_version = int(idx_stats.get("builder_version") or 0)
                    if idx_version < BUILDER_VERSION:
                        msg = (
                            f"Index built with v{idx_version}, current v{BUILDER_VERSION} — "
                            f'new helpers available after rebuild: rlm-bsl-index index build "{resolved}"'
                        )
                        idx_warnings.append(msg)
                        logger.warning("rlm_start: session=%s %s", session_id, msg)
        except Exception as e:
            if idx_reader is not None:
                try:
                    idx_reader.close()
                except Exception:
                    pass
                idx_reader = None
            logger.warning("rlm_start: session=%s index load failed: %s", session_id, e)
        t_index = time.monotonic() - t_step

        # --- Format + extension detection (fast path from index or disk) ---
        startup_meta = None
        if idx_reader is not None and idx_status == IndexStatus.FRESH:
            startup_meta = idx_reader.get_startup_meta()

        if startup_meta is not None:
            # Fast path: reconstruct from cached index metadata
            t_step = time.monotonic()
            format_info = FormatInfo(
                primary_format=SourceFormat(startup_meta["source_format"]),
                root_path=resolved,
                bsl_file_count=int(startup_meta["shallow_bsl_count"]),
                has_configuration_xml=startup_meta.get("has_configuration_xml") == "1",
                metadata_categories_found=[],
            )
            t_format = time.monotonic() - t_step

            # Live extension scan (always fresh, <0.5s)
            t_step = time.monotonic()
            ext_context = detect_extension_context(resolved)
            t_ext = time.monotonic() - t_step

            t_step = time.monotonic()
            ext_overrides: dict[str, list[dict]] = _auto_scan_overrides(ext_context)
            t_overrides = time.monotonic() - t_step

            src_format = "index"
            src_ext = "live"
        else:
            # Disk path: full detection
            t_step = time.monotonic()
            format_info = detect_format(resolved)
            t_format = time.monotonic() - t_step

            t_step = time.monotonic()
            ext_context = detect_extension_context(resolved)
            t_ext = time.monotonic() - t_step

            # Auto-scan extension overrides (extensions are small, <1s)
            t_step = time.monotonic()
            ext_overrides = _auto_scan_overrides(ext_context)
            t_overrides = time.monotonic() - t_step

            src_format = "disk"
            src_ext = "disk"

            # Drift check: compare shallow counts (same methodology)
            if idx_reader is not None:
                _sm = idx_reader.get_startup_meta()
                stored_shallow = int(_sm["shallow_bsl_count"]) if _sm and _sm.get("shallow_bsl_count") else None
                if stored_shallow is not None and format_info.bsl_file_count:
                    drift = abs(format_info.bsl_file_count - stored_shallow) / max(stored_shallow, 1)
                    if drift > 0.05:
                        idx_warnings.append(
                            f"File count drift (shallow): index {stored_shallow}, "
                            f"disk {format_info.bsl_file_count} — "
                            "run 'rlm-bsl-index index build' if significant changes were made"
                        )

        logger.info(
            "rlm_start: session=%s format=%s bsl_files=%d config_role=%s overrides=%d",
            session_id,
            format_info.format_label,
            format_info.bsl_file_count,
            ext_context.current.role.value,
            sum(len(v) for v in ext_overrides.values()),
        )

        # Pre-import openai in background while Sandbox builds (~13s on slow PCs)
        if os.environ.get("RLM_LLM_BASE_URL"):
            threading.Thread(target=warmup_openai_import, daemon=True).start()

        # Determine if index is authoritative for zero-callers results
        _callers_authoritative = idx_status == IndexStatus.FRESH and idx_reader is not None and idx_reader.has_calls

        t_step = time.monotonic()
        sandbox = Sandbox(
            base_path=resolved,
            max_output_chars=max_output_chars,
            execution_timeout_seconds=execution_timeout_seconds,
            format_info=format_info,
            idx_reader=idx_reader,
            idx_zero_callers_authoritative=_callers_authoritative,
        )
        has_llm_tools = _install_session_llm_tools(session, sandbox)
        t_sandbox = time.monotonic() - t_step
        logger.info(
            "rlm_start: session=%s sandbox ready, llm_tools=%s index=%s",
            session_id,
            has_llm_tools,
            idx_reader is not None,
        )

        # Auto-detect custom prefixes — fast path from index, fallback to glob scan
        t_step = time.monotonic()
        detected_prefixes: list[str] = []
        src_prefixes = "none"
        if idx_reader is not None:
            try:
                detected_prefixes = idx_reader.get_detected_prefixes()
                if detected_prefixes:
                    src_prefixes = "index"
            except Exception:
                pass
        if not detected_prefixes:
            _prefix_fn = sandbox._namespace.get("_detected_prefixes")
            if callable(_prefix_fn):
                try:
                    detected_prefixes = _prefix_fn()
                    if detected_prefixes:
                        src_prefixes = "fallback"
                except Exception:
                    pass
        t_prefixes = time.monotonic() - t_step

        bsl_registry = sandbox._namespace.get("_registry") or {}
        t_step = time.monotonic()
        strategy = get_strategy(
            effort,
            format_info,
            detected_prefixes,
            ext_context,
            ext_overrides,
            registry=bsl_registry,
            idx_stats=idx_stats,
            idx_warnings=idx_warnings,
            query=query,
        )
        t_strategy = time.monotonic() - t_step

        with _sandboxes_lock:
            _sandboxes[session_id] = sandbox
            if idx_reader is not None:
                _idx_readers[session_id] = idx_reader
    except Exception as e:
        logger.error("rlm_start: session=%s failed: %s", session_id, e, exc_info=True)
        session_manager.end(session_id)
        return json.dumps(
            {"error": f"Session init failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    # Build available_functions from registry (BSL helpers) + static IO helpers
    available_functions = [entry["sig"] for entry in bsl_registry.values()]
    available_functions.extend(
        [
            "read_file(path) -> str (numbered: '  42 | code')",
            "read_files(paths) -> dict[path, str] (numbered: '  42 | code')",
            "grep(pattern, path='.') -> list[dict] keys: file, line, text",
            "grep_summary(pattern, path='.') -> compact grouped string",
            "grep_read(pattern, path='.', max_files=10, context_lines=0) -> {matches, files (numbered), summary}",
            "glob_files(pattern) -> list[str]",
            "tree(path='.', max_depth=3) -> str",
            "find_files(name) -> list[str]",
        ]
    )
    if has_llm_tools:
        available_functions.extend(
            [
                "llm_query(prompt, context='')",
                "llm_query_batched(prompts, context='')",
            ]
        )

    response: dict = {
        "session_id": session_id,
        "warnings": ext_context.warnings,
        "config_format": format_info.format_label,
        "extension_context": {
            "is_extension": ext_context.current.role.value == "extension",
            "config_role": ext_context.current.role.value,
            "current_name": ext_context.current.name,
            "current_purpose": ext_context.current.purpose or None,
            "current_prefix": ext_context.current.name_prefix or None,
            "nearby_extensions": [
                {
                    "name": e.name,
                    "purpose": e.purpose,
                    "prefix": e.name_prefix,
                    "path": e.path,
                    "overrides": ext_overrides.get(e.path, []),
                }
                for e in ext_context.nearby_extensions
            ],
            "nearby_main": (
                {"name": ext_context.nearby_main.name, "path": ext_context.nearby_main.path}
                if ext_context.nearby_main
                else None
            ),
            "own_overrides": ext_overrides.get("self", []) if ext_context.current.role.value == "extension" else None,
        },
        "detected_custom_prefixes": detected_prefixes,
        "index": {
            "loaded": idx_reader is not None,
            "index_check": "quick",
            "methods": idx_stats.get("methods") if idx_stats else None,
            "calls": idx_stats.get("calls") if idx_stats else None,
            "has_fts": idx_stats.get("has_fts", False) if idx_stats else False,
            "config_name": idx_stats.get("config_name") if idx_stats else None,
            "config_version": idx_stats.get("config_version") if idx_stats else None,
            "warnings": idx_warnings,
        },
        "metadata": metadata,
        "limits": {
            "max_llm_calls": session.max_llm_calls,
            "max_execute_calls": session.max_execute_calls,
            "execution_timeout_seconds": execution_timeout_seconds,
        },
        "available_functions": available_functions,
        "strategy": strategy,
    }
    if project_hint:
        response["project_hint"] = project_hint
    logger.info(
        "rlm_start: session=%s timings: format=%.1fs ext=%.1fs overrides=%.1fs index=%.1fs sandbox=%.1fs prefixes=%.1fs strategy=%.1fs",
        session_id,
        t_format,
        t_ext,
        t_overrides,
        t_index,
        t_sandbox,
        t_prefixes,
        t_strategy,
    )
    logger.info(
        "rlm_start: session=%s sources: format=%s ext=%s prefixes=%s",
        session_id,
        src_format,
        src_ext,
        src_prefixes,
    )
    result_json = json.dumps(response, ensure_ascii=False)
    out_chars = len(result_json)
    session.total_out_chars += out_chars
    logger.info(
        "rlm_start: session=%s completed in %.2fs out_chars=%d out_tokens~%d",
        session_id,
        time.monotonic() - t0,
        out_chars,
        int(out_chars / 1.75),
    )
    return result_json


def _format_helper_summary(helper_calls: list[HelperCall], threshold: float) -> tuple[str, int]:
    """Format helper calls for log. Returns (summary_string, notable_count)."""
    grouped: dict[str, list[float]] = {}
    for h in helper_calls:
        if h.elapsed >= threshold:
            grouped.setdefault(h.name, []).append(h.elapsed)
    parts = ", ".join(
        f"{name}({times[0]:.1f}s)" if len(times) == 1 else f"{name}({len(times)}\u00d7, total={sum(times):.1f}s)"
        for name, times in grouped.items()
    )
    return parts, len(grouped)


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
        return json.dumps(
            {"error": (f"Execution call limit exceeded: {session.execute_calls} >= {session.max_execute_calls}")},
            ensure_ascii=False,
        )

    session.execute_calls += 1
    result = sandbox.execute(code)

    elapsed = time.monotonic() - t0
    # Log helper calls with timing (grouped by name)
    helpers_summary = ""
    if result.helper_calls:
        total = len(result.helper_calls)
        log_all = os.environ.get("RLM_LOG_HELPERS", "").lower() == "all"
        threshold = 0.0 if log_all else 0.1
        parts, notable_count = _format_helper_summary(result.helper_calls, threshold)
        if notable_count:
            helpers_summary = f" [{total} helpers: {parts}]"
        else:
            helpers_summary = f" [{total} helpers]"
    session.total_in_chars += len(code)

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
        # Build excluded_vars from registry + static helpers
        bsl_reg = sandbox._namespace.get("_registry") or {}
        excluded_vars = set(bsl_reg.keys()) | {
            "_detected_prefixes",
            "_registry",
            "read_file",
            "read_files",
            "grep",
            "grep_summary",
            "grep_read",
            "glob_files",
            "tree",
            "find_files",
            "llm_query",
            "llm_query_batched",
        }
        new_vars = sorted(v for v in (current_vars - previous_vars) if v not in excluded_vars)
        session._last_reported_vars = current_vars

        response["variables"] = sorted(v for v in current_vars if v not in excluded_vars)
        response["total_variables"] = len(response["variables"])
        response["new_variables"] = new_vars[:max_new_variables]
        if len(new_vars) > max_new_variables:
            response["new_variables_truncated_count"] = len(new_vars) - max_new_variables

    result_json = json.dumps(response, ensure_ascii=False)
    out_chars = len(result_json)
    session.total_out_chars += out_chars
    logger.info(
        "rlm_execute: session=%s call=%d/%d error=%s elapsed=%.2fs out_chars=%d out_tokens~%d%s",
        session_id,
        session.execute_calls,
        session.max_execute_calls,
        bool(result.error),
        elapsed,
        out_chars,
        int(out_chars / 1.75),
        helpers_summary,
    )
    return result_json


def _rlm_end(session_id: str) -> str:
    session = session_manager.get(session_id)
    if session:
        total_chars = session.total_in_chars + session.total_out_chars
        logger.info(
            "rlm_end: session=%s calls=%d in_chars=%d out_chars=%d total_chars=%d total_tokens~%d",
            session_id,
            session.execute_calls,
            session.total_in_chars,
            session.total_out_chars,
            total_chars,
            int(total_chars / 1.75),
        )
    else:
        logger.info("rlm_end: session=%s (not found)", session_id)
    session_manager.end(session_id)
    with _sandboxes_lock:
        _sandboxes.pop(session_id, None)
        reader = _idx_readers.pop(session_id, None)
    if reader is not None:
        try:
            reader.close()
        except Exception:
            pass
    return json.dumps({"success": True}, ensure_ascii=False)


@mcp.tool()
async def rlm_start(
    query: Annotated[str, Field(description="What you want to find or analyze in the BSL codebase")],
    path: Annotated[str | None, Field(description="Absolute path to the 1C BSL codebase directory")] = None,
    project: Annotated[str | None, Field(description="Project name from the registry (alternative to path)")] = None,
    effort: Annotated[
        str,
        Field(
            description="Analysis depth: low (single quick lookup), medium (standard), high (deep trace, RECOMMENDED for multi-aspect analysis), max (exhaustive)"
        ),
    ] = "high",
    max_output_chars: Annotated[
        int, Field(description="Max characters per execute output", ge=100, le=100_000)
    ] = 15_000,
    max_llm_calls: Annotated[
        int | None, Field(description="Override max llm_query calls (default from effort level)")
    ] = None,
    max_execute_calls: Annotated[
        int | None, Field(description="Override max rlm_execute calls (default from effort level)")
    ] = None,
    execution_timeout_seconds: Annotated[
        int, Field(description="Per-rlm_execute timeout in seconds", ge=1, le=300)
    ] = 45,
    include_metadata: Annotated[
        bool,
        Field(
            description="Scan directory and include file counts/types in response (slow on large configs, disabled by default)"
        ),
    ] = False,
) -> str:
    """Start a BSL code exploration session on a 1C codebase. Returns JSON with session_id.
    You can specify either 'path' (absolute filesystem path) or 'project' (name from the project registry).
    If you don't know the path, call rlm_projects(action='list') first to see registered projects,
    then use rlm_start(project='name', query='...').
    If the user mentions a project by name -- always try project parameter first.
    If the path is not registered, the response will include a project_hint suggesting to register it.
    Then call rlm_execute(session_id, code) where code is Python that calls helper functions and uses print() to output results.
    IMPORTANT: For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first."""
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
            project=project,
        )
    )


@mcp.tool()
async def rlm_execute(
    session_id: Annotated[str, Field(description="Session ID from rlm_start")],
    code: Annotated[
        str,
        Field(
            description=(
                "Python code to execute. IMPORTANT: Batch multiple related operations into each call. "
                "A good call does: grep -> read top matches -> extract patterns -> print summary. "
                "A bad call does just one grep or one read_file. Variables persist between calls."
            )
        ),
    ],
    detail_level: Annotated[
        Literal["compact", "usage", "full"],
        Field(
            description="Response payload level: compact=stdout+error, usage=add usage metrics, full=add variable details"
        ),
    ] = "compact",
    max_new_variables: Annotated[
        int,
        Field(
            description="When detail_level=full, cap returned new_variables list to this size",
            ge=1,
            le=200,
        ),
    ] = 20,
) -> str:
    """Execute Python code in the BSL sandbox. The 'code' parameter is Python code. Call helper functions and use print() to see results. Variables persist between calls. Example: code="modules = find_module('MyModule')\\nfor m in modules:\\n    print(m['path'])". BSL helpers: help, find_module, find_by_type, extract_procedures, find_exports, safe_grep, read_procedure, find_callers, find_callers_context, parse_object_xml. Standard: read_file, read_files, grep, grep_summary, grep_read, glob_files, tree. CRITICAL: grep on path='.' ALWAYS times out on large 1C configs. Use find_module() first."""
    return await anyio.to_thread.run_sync(lambda: _rlm_execute(session_id, code, detail_level, max_new_variables))


@mcp.tool()
async def rlm_end(
    session_id: Annotated[str, Field(description="Session ID to end")],
) -> str:
    """End an RLM exploration session and free resources."""
    return await anyio.to_thread.run_sync(lambda: _rlm_end(session_id))


@mcp.tool()
async def rlm_projects(
    action: Annotated[
        Literal["list", "add", "remove", "rename", "update"],
        Field(description="Action to perform on the project registry"),
    ],
    name: Annotated[str | None, Field(description="Project name (required for add/remove/rename/update)")] = None,
    path: Annotated[str | None, Field(description="Absolute filesystem path to 1C sources (required for add)")] = None,
    description: Annotated[str | None, Field(description="Optional project description")] = None,
    new_name: Annotated[str | None, Field(description="New name for rename action")] = None,
    password: Annotated[
        str | None,
        Field(description="Project password for index management. Required for build/update/drop via rlm_index."),
    ] = None,
    clear_password: Annotated[
        bool, Field(description="Remove project password (disables index management via MCP)")
    ] = False,
) -> str:
    """Manage the server-side project registry -- a mapping of human-readable project names to filesystem paths.
    Use 'list' to see all registered 1C projects, 'add' to register a new project (name + path + optional description),
    'remove' to unregister, 'rename' to change a project's display name, 'update' to change path or description.
    After registering a project, you can open sessions via rlm_start(project='name') instead of specifying the full path.
    When the user mentions a project by name, call list first to find available projects.
    Set password when adding/updating to enable index management (build/update/drop) via rlm_index."""
    return await anyio.to_thread.run_sync(
        lambda: _rlm_projects(
            action=action,
            name=name,
            path=path,
            description=description,
            new_name=new_name,
            password=password,
            clear_password=clear_password,
        )
    )


def _rlm_projects(
    action: str,
    name: str | None = None,
    path: str | None = None,
    description: str | None = None,
    new_name: str | None = None,
    password: str | None = None,
    clear_password: bool = False,
) -> str:
    from rlm_tools_bsl.projects import RegistryCorruptedError, get_registry

    try:
        reg = get_registry()

        # Translate host paths to container paths (Docker)
        if path:
            path = _resolve_path_map(path)

        # Resolve mapped drives (Windows service in Session 0)
        if path and not os.path.isdir(path):
            unc = _resolve_mapped_drive(path)
            if unc:
                path = str(pathlib.Path(unc).resolve())

        if action == "list":
            return json.dumps({"projects": reg.list_projects()}, ensure_ascii=False)

        if action == "add":
            if not name:
                return json.dumps({"error": "name is required for 'add'"}, ensure_ascii=False)
            if not path:
                return json.dumps({"error": "path is required for 'add'"}, ensure_ascii=False)
            entry = reg.add(name, path, description or "", password=password)
            return json.dumps({"added": entry}, ensure_ascii=False)

        if action == "remove":
            if not name:
                return json.dumps({"error": "name is required for 'remove'"}, ensure_ascii=False)
            entry = reg.remove(name)
            return json.dumps({"removed": entry}, ensure_ascii=False)

        if action == "rename":
            if not name:
                return json.dumps({"error": "name is required for 'rename'"}, ensure_ascii=False)
            if not new_name:
                return json.dumps({"error": "new_name is required for 'rename'"}, ensure_ascii=False)
            entry = reg.rename(name, new_name)
            return json.dumps({"renamed": entry}, ensure_ascii=False)

        if action == "update":
            if not name:
                return json.dumps({"error": "name is required for 'update'"}, ensure_ascii=False)
            entry = reg.update(
                name, path=path, description=description, password=password, clear_password=clear_password
            )
            return json.dumps({"updated": entry}, ensure_ascii=False)

        return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)

    except RegistryCorruptedError as exc:
        return json.dumps(
            {"error": f"Registry file is corrupted: {exc}. Run rlm_projects(action='list') after fixing the file."},
            ensure_ascii=False,
        )
    except (ValueError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def rlm_index(
    action: Annotated[
        Literal["build", "update", "info", "drop"],
        Field(description="Action to perform on the index"),
    ],
    path: Annotated[str | None, Field(description="Root path to 1C configuration sources")] = None,
    project: Annotated[str | None, Field(description="Project name from the registry")] = None,
    no_calls: Annotated[bool, Field(description="Skip call graph (build only)")] = False,
    no_metadata: Annotated[bool, Field(description="Skip L2 metadata (build only)")] = False,
    no_fts: Annotated[bool, Field(description="Skip FTS5 full-text index (build only)")] = False,
    no_synonyms: Annotated[bool, Field(description="Skip object synonyms (build only)")] = False,
    confirm: Annotated[
        str | None,
        Field(
            description="Project password for build/update/drop confirmation. "
            "Ask the user for their project password when server returns approval_required."
        ),
    ] = None,
) -> str:
    """Manage the BSL method index — build, update, get info, or drop.
    Full parity with CLI 'rlm-bsl-index'. Use 'build' to create index from scratch,
    'update' for incremental refresh, 'info' for statistics, 'drop' to remove the index.
    Provide either 'path' (filesystem path) or 'project' (registered project name).
    'build', 'update' and 'drop' require a registered project with password —
    ask the user for the project password."""
    if action in ("build", "update", "drop"):
        from rlm_tools_bsl.projects import RegistryCorruptedError, get_registry

        # MCP: path запрещён для admin-действий
        if path is not None:
            return json.dumps(
                {
                    "error": f"Action '{action}' requires a registered project with password. "
                    "Use project=... instead of path=... "
                    "Register the project first: rlm_projects(action='add', name='...', path='...', password='...')"
                },
                ensure_ascii=False,
            )

        if not project:
            return json.dumps(
                {"error": f"Action '{action}' requires project=... (registered project with password)."},
                ensure_ascii=False,
            )

        # Resolve project name
        try:
            reg = get_registry()
            matches, method = reg.resolve(project)
        except RegistryCorruptedError as exc:
            return json.dumps(
                {"error": f"Registry file is corrupted: {exc}. Run rlm_projects(action='list') after fixing the file."},
                ensure_ascii=False,
            )
        if not matches:
            return json.dumps({"error": f"Project not found: {project}"}, ensure_ascii=False)
        if len(matches) > 1:
            names = [m["name"] for m in matches]
            return json.dumps({"error": f"Ambiguous project: {names}"}, ensure_ascii=False)
        if method == "fuzzy":
            return json.dumps({"error": f"Did you mean '{matches[0]['name']}'?"}, ensure_ascii=False)

        project_name = matches[0]["name"]

        # Password check
        if not reg.has_password(project_name):
            return json.dumps(
                {
                    "error": "Project has no password configured. "
                    "Index management via MCP requires a project password. "
                    "Set it: rlm_projects(action='update', name='...', password='...')"
                },
                ensure_ascii=False,
            )

        if not confirm or not reg.verify_password(project_name, confirm):
            return json.dumps(
                {
                    "approval_required": True,
                    "action": action,
                    "project": project_name,
                    "message": "Введите пароль проекта для подтверждения управления индексами. "
                    "Ask the user for their project password. Do NOT proceed without it.",
                },
                ensure_ascii=False,
            )

        # Password correct — proceed with project (not path)

    return await anyio.to_thread.run_sync(
        lambda: _rlm_index(
            action=action,
            path=path,
            project=project,
            no_calls=no_calls,
            no_metadata=no_metadata,
            no_fts=no_fts,
            no_synonyms=no_synonyms,
        )
    )


def _rlm_index(
    action: str,
    path: str | None = None,
    project: str | None = None,
    no_calls: bool = False,
    no_metadata: bool = False,
    no_fts: bool = False,
    no_synonyms: bool = False,
) -> str:
    from rlm_tools_bsl.projects import RegistryCorruptedError, get_registry
    from rlm_tools_bsl.bsl_index import IndexBuilder, IndexReader, get_index_db_path

    # --- Resolve path ---
    if path is None and project is None:
        return json.dumps({"error": "Either 'path' or 'project' must be provided"}, ensure_ascii=False)

    resolved_project_name: str | None = None
    if path is None:
        try:
            reg = get_registry()
            matches, method = reg.resolve(project)  # type: ignore[arg-type]
        except RegistryCorruptedError as exc:
            return json.dumps(
                {"error": f"Registry file is corrupted: {exc}. Run rlm_projects(action='list') after fixing the file."},
                ensure_ascii=False,
            )
        if not matches:
            all_projects = reg.list_projects()
            available = [{"name": p["name"], "description": p.get("description", "")} for p in all_projects]
            return json.dumps(
                {"error": f"Project not found: {project}", "available_projects": available}, ensure_ascii=False
            )
        if len(matches) > 1:
            ambiguous = [{"name": p["name"], "description": p.get("description", "")} for p in matches]
            return json.dumps({"error": f"Ambiguous project name: {project}", "matches": ambiguous}, ensure_ascii=False)
        if method == "fuzzy":
            return json.dumps({"error": f"Did you mean '{matches[0]['name']}'?"}, ensure_ascii=False)
        path = matches[0]["path"]
        resolved_project_name = matches[0]["name"]
    else:
        path = _resolve_path_map(path)

    # Resolve mapped drives (Windows service)
    resolved = str(pathlib.Path(path).resolve())
    if not os.path.isdir(resolved):
        unc_path = _resolve_mapped_drive(path)
        if unc_path:
            resolved = str(pathlib.Path(unc_path).resolve())
        if not os.path.isdir(resolved):
            return json.dumps({"error": f"Path not found: {path}"}, ensure_ascii=False)

    try:
        if action == "build":
            t0 = time.monotonic()
            builder = IndexBuilder()
            db_path = builder.build(
                resolved,
                build_calls=not no_calls,
                build_metadata=not no_metadata,
                build_fts=not no_fts,
                build_synonyms=not no_synonyms,
            )
            elapsed = time.monotonic() - t0
            result = {
                "action": "build",
                "path": resolved,
                "db_path": str(db_path),
                "elapsed_seconds": round(elapsed, 1),
            }
            if resolved_project_name:
                result["project"] = resolved_project_name
            return json.dumps(result, ensure_ascii=False)

        if action == "update":
            t0 = time.monotonic()
            builder = IndexBuilder()
            delta = builder.update(resolved)
            elapsed = time.monotonic() - t0
            result = {"action": "update", "path": resolved, "elapsed_seconds": round(elapsed, 1), **delta}
            if resolved_project_name:
                result["project"] = resolved_project_name
            return json.dumps(result, ensure_ascii=False)

        if action == "info":
            db_path = get_index_db_path(resolved)
            if not db_path.exists():
                return json.dumps({"error": "Index not found", "path": resolved}, ensure_ascii=False)
            reader = IndexReader(str(db_path))
            try:
                stats = reader.get_statistics()
                result = {"action": "info", "path": resolved, **stats}
                if resolved_project_name:
                    result["project"] = resolved_project_name
                return json.dumps(result, ensure_ascii=False)
            finally:
                reader.close()

        if action == "drop":
            db_path = get_index_db_path(resolved)
            if not db_path.exists():
                return json.dumps({"error": "Index not found", "path": resolved}, ensure_ascii=False)
            db_path.unlink()
            # Remove parent dir if empty
            try:
                db_path.parent.rmdir()
            except OSError:
                pass
            result = {"action": "drop", "path": resolved, "dropped": str(db_path)}
            if resolved_project_name:
                result["project"] = resolved_project_name
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)

    except FileNotFoundError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("rlm_index error: action=%s path=%s", action, resolved)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


class _HealthLogFilter(logging.Filter):
    """Suppress noisy uvicorn access-log lines for GET /health."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "GET /health" not in msg


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
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger("uvicorn.access").addFilter(_HealthLogFilter())
    logger.info("File logging enabled: %s", log_path)


def _warmup_imports():
    """Pre-import heavy modules so first rlm_start is fast. Best-effort."""
    _t0 = time.monotonic()
    try:
        import rlm_tools_bsl.bsl_helpers  # noqa: F401
        import rlm_tools_bsl.bsl_xml_parsers  # noqa: F401
        import rlm_tools_bsl.bsl_index  # noqa: F401
        import rlm_tools_bsl.helpers  # noqa: F401

        warmup_openai_import()
    except Exception:
        logger.debug("warmup: import error (non-critical)", exc_info=True)
    logger.info("warmup: completed in %.1fs", time.monotonic() - _t0)


def main():
    global session_manager
    from rlm_tools_bsl._config import load_project_env

    load_project_env()

    session_manager = build_session_manager_from_env()

    parser = argparse.ArgumentParser(description="rlm-tools-bsl MCP server")
    parser.add_argument(
        "--version",
        "-V",
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

        # Disable DNS rebinding protection for external interfaces —
        # when binding to 0.0.0.0 the Host header can be any IP.
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )

    if args.transport != "stdio":
        logger.info(
            "transport=%s stateless_http=%s host=%s port=%s",
            args.transport,
            mcp.settings.stateless_http,
            getattr(mcp.settings, "host", "?"),
            getattr(mcp.settings, "port", "?"),
        )

    threading.Thread(target=_warmup_imports, daemon=True).start()
    mcp.run(transport=args.transport)
