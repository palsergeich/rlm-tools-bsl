from __future__ import annotations

import io
import contextlib
import builtins
import pathlib
import signal
import threading
import traceback
from contextlib import contextmanager
from dataclasses import dataclass

from rlm_tools_bsl.helpers import make_helpers
from rlm_tools_bsl.bsl_helpers import make_bsl_helpers


ALLOWED_MODULES = frozenset({
    "re", "json", "collections", "math",
    "fnmatch", "itertools", "functools", "operator", "string",
    "textwrap", "difflib", "statistics",
})

BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "breakpoint",
    "exit", "quit", "input",
})


@dataclass
class ExecutionResult:
    stdout: str
    error: str | None
    variables: list[str]


def _make_restricted_import(allowed: frozenset[str]):
    original_import = builtins.__import__

    def restricted_import(name, *args, **kwargs):
        if name not in allowed and name.split(".")[0] not in allowed:
            raise ImportError(f"Import of '{name}' is not allowed in the sandbox")
        return original_import(name, *args, **kwargs)

    return restricted_import


class Sandbox:
    def __init__(
        self,
        base_path: str,
        max_output_chars: int = 15_000,
        execution_timeout_seconds: int = 45,
        format_info=None,
        idx_reader=None,
    ):
        self._base_path = base_path
        self._max_output_chars = max_output_chars
        self._execution_timeout_seconds = execution_timeout_seconds
        self._format_info = format_info
        self._idx_reader = idx_reader
        self._namespace: dict = {}
        self._resolve_safe = None
        self._setup_namespace()

    def _setup_namespace(self) -> None:
        safe_builtins = {
            k: v for k, v in builtins.__dict__.items()
            if k not in BLOCKED_BUILTINS
        }
        safe_builtins["__import__"] = _make_restricted_import(ALLOWED_MODULES)

        original_open = builtins.open

        def restricted_open(file, mode="r", *args, **kwargs):
            if any(c in mode for c in "wxa+"):
                raise PermissionError(f"Write access denied in sandbox (mode='{mode}')")

            if self._resolve_safe is None:
                raise RuntimeError("Sandbox path resolver was not initialized")

            if isinstance(file, int):
                raise PermissionError("File descriptor access is not allowed in sandbox")

            # Keep read access scoped to the sandbox root.
            safe_path = self._resolve_safe(str(pathlib.Path(file)))
            return original_open(safe_path, mode, *args, **kwargs)

        safe_builtins["open"] = restricted_open

        self._namespace["__builtins__"] = safe_builtins

        helpers, self._resolve_safe = make_helpers(self._base_path)
        self._namespace.update(helpers)

        if self._format_info is not None:
            bsl_helpers = make_bsl_helpers(
                base_path=self._base_path,
                resolve_safe=self._resolve_safe,
                read_file_fn=helpers["read_file"],
                grep_fn=helpers["grep"],
                glob_files_fn=helpers["glob_files"],
                format_info=self._format_info,
                idx_reader=self._idx_reader,
            )
            self._namespace.update(bsl_helpers)

    @contextmanager
    def _execution_timeout(self):
        if self._execution_timeout_seconds <= 0:
            yield
            return

        if (
            threading.current_thread() is threading.main_thread()
            and hasattr(signal, "SIGALRM")
        ):
            # Unix: signal-based timeout (precise, interrupts C extensions)
            def _raise_timeout(_signum, _frame):
                raise TimeoutError(
                    f"Execution timed out after {self._execution_timeout_seconds} seconds"
                )

            previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _raise_timeout)
            signal.setitimer(signal.ITIMER_REAL, self._execution_timeout_seconds)
            try:
                yield
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
        else:
            # Windows / non-main thread: threading-based timeout
            # Sets a flag that we check — cannot interrupt blocking I/O,
            # but catches long-running Python loops.
            import ctypes
            timed_out = threading.Event()
            target_tid = threading.current_thread().ident

            def _timeout_watchdog():
                timed_out.set()
                if target_tid is not None:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_ulong(target_tid),
                        ctypes.py_object(TimeoutError),
                    )

            timer = threading.Timer(self._execution_timeout_seconds, _timeout_watchdog)
            timer.daemon = True
            timer.start()
            try:
                yield
            finally:
                timer.cancel()
                if timed_out.is_set():
                    raise TimeoutError(
                        f"Execution timed out after {self._execution_timeout_seconds} seconds"
                    )

    def execute(self, code: str) -> ExecutionResult:
        stdout_capture = io.StringIO()
        error = None

        try:
            with contextlib.redirect_stdout(stdout_capture):
                with self._execution_timeout():
                    exec(code, self._namespace)
        except Exception:
            error = traceback.format_exc()
            error = self._add_error_hints(error, code)

        stdout = stdout_capture.getvalue()
        if len(stdout) > self._max_output_chars:
            stdout = stdout[:self._max_output_chars] + "\n... [output truncated]"

        return ExecutionResult(
            stdout=stdout,
            error=error,
            variables=self.list_variables(),
        )

    @staticmethod
    def _add_error_hints(error: str, code: str) -> str:
        """Append actionable hints to common errors."""
        hints: list[str] = []

        if "FileNotFoundError" in error or "No such file" in error:
            if "parse_object_xml" in code:
                hints.append(
                    "HINT: parse_object_xml accepts directory paths too: "
                    "parse_object_xml('Documents/Name') — it auto-finds the XML."
                )
            elif ".xml" in code or ".bsl" in code:
                hints.append(
                    "HINT: Use find_module('Name') or glob_files('**/pattern') "
                    "to discover correct file paths first."
                )

        if "TimeoutError" in error:
            hints.append(
                "HINT: Operation timed out. For large configs, avoid composite helpers "
                "(analyze_document_flow, analyze_object) and call individual helpers instead: "
                "find_register_movements, find_event_subscriptions, find_callers_context."
            )

        if "NameError" in error:
            hints.append(
                "HINT: Call help() to see available functions. "
                "Variables persist between rlm_execute calls."
            )

        if "import" in error.lower() and "restricted" in error.lower():
            hints.append(
                "HINT: Only standard library modules are allowed. "
                "Use built-in helpers instead of external libraries."
            )

        if hints:
            error = error.rstrip() + "\n\n" + "\n".join(hints)

        return error

    def list_variables(self) -> list[str]:
        return [
            k for k in self._namespace
            if not k.startswith("_") and k != "__builtins__"
        ]
