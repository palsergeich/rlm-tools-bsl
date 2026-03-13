"""Windows Service implementation for rlm-tools-bsl using pywin32.

Architecture:
  pythonservice.exe (system Python) imports this module via PYTHONPATH set
  in the service registry.  SvcDoRun spawns rlm-tools-bsl.exe (uv tool env)
  as a child process instead of importing the server directly, so the HTTP
  server always runs in its own isolated Python environment.
"""
import os
import pathlib
import subprocess
import threading

import win32event
import win32service
import win32serviceutil

from rlm_tools_bsl.service import CONFIG_FILE, load_config, save_config

SERVICE_NAME = "rlm-tools-bsl"
SERVICE_DISPLAY = "RLM Tools BSL (MCP HTTP Server)"
SERVICE_DESC = "RLM-инструменты для анализа 1C BSL-кода. Предназначены для экономии расхода токенов и контекста при анализе BSL-проектов"


class RlmWindowsService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY
    _svc_description_ = SERVICE_DESC

    def SvcDoRun(self) -> None:
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)

    def _run_server(self) -> None:
        cfg = load_config()
        exe = cfg.get("exe_path") or "rlm-tools-bsl"
        env = os.environ.copy()
        env_file = cfg.get("env_file")
        if env_file and pathlib.Path(env_file).exists():
            _load_env_file(env_file, env)
        self._proc = subprocess.Popen(
            [exe, "--transport", "streamable-http", "--host", cfg["host"], "--port", str(cfg["port"])],
            env=env,
        )
        self._proc.wait()

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        proc = self._proc
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        win32event.SetEvent(self._stop_event)


def _load_env_file(path: str, env: dict) -> None:
    """Parse .env file and merge variables into env (no override of existing vars)."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                env.setdefault(k, v)
    except OSError:
        pass


def _set_service_environment(service_name: str, site_packages: str, config_file: str) -> None:
    """Set Environment REG_MULTI_SZ value on the service registry key.

    Windows SCM reads the 'Environment' value (REG_MULTI_SZ) directly under
    HKLM\\SYSTEM\\CurrentControlSet\\Services\\<name> and injects those
    variables into the service process environment at start.

    We set:
      PYTHONPATH  — so pythonservice.exe (system Python) can import rlm_tools_bsl
      RLM_CONFIG_FILE — so load_config() finds the user's service.json
                        (LocalSystem has a different home dir)
    """
    import winreg

    key_path = rf"SYSTEM\CurrentControlSet\Services\{service_name}"
    env_vars = [
        f"PYTHONPATH={site_packages}",
        f"RLM_CONFIG_FILE={config_file}",
    ]
    try:
        with winreg.OpenKeyEx(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Environment", 0, winreg.REG_MULTI_SZ, env_vars)
        print(f"PYTHONPATH set in service environment: {site_packages}")
        print(f"RLM_CONFIG_FILE set in service environment: {config_file}")
    except Exception as exc:
        print(f"Warning: could not set Environment in registry: {exc}")


def install(host: str, port: int, env_file: str | None) -> None:
    import shutil
    import sys

    # Locate rlm-tools-bsl.exe in the current (uv tool) Python environment.
    # We try two strategies: PATH lookup and sibling of sys.executable.
    exe_path: str | None = shutil.which("rlm-tools-bsl")
    if not exe_path:
        candidate = pathlib.Path(sys.executable).parent / "rlm-tools-bsl.exe"
        if candidate.exists():
            exe_path = str(candidate)

    if exe_path:
        print(f"Found rlm-tools-bsl.exe: {exe_path}")
    else:
        print("Warning: rlm-tools-bsl.exe not found in PATH; service may fail to start.")

    # site-packages of the current (uv tool) env — needed for PYTHONPATH in registry
    # _service_win.py lives at  <site-packages>/rlm_tools_bsl/_service_win.py
    site_packages = str(pathlib.Path(__file__).parent.parent)

    # pythonservice.exe needs several DLLs next to it that aren't on the
    # DLL search path in an isolated uv tool environment:
    #   - pywintypes*.dll, pythoncom*.dll  (pywin32, in pywin32_system32/)
    #   - python3.dll, python3XX.dll       (Python runtime, in sys.prefix or exe dir)
    # site_packages = .../Lib/site-packages, pythonservice.exe is at env root (2 levels up)
    svc_dir = pathlib.Path(site_packages).parent.parent
    dlls_to_copy: list[pathlib.Path] = []

    # pywin32 DLLs
    pywin32_sys32 = pathlib.Path(site_packages) / "pywin32_system32"
    if pywin32_sys32.is_dir():
        dlls_to_copy.extend(pywin32_sys32.glob("*.dll"))

    # Python runtime DLLs (python3.dll + python3XX.dll)
    # In venvs/uv tool envs, DLLs are in base_prefix, not prefix
    for py_dir in dict.fromkeys([
        pathlib.Path(sys.base_prefix),
        pathlib.Path(sys.prefix),
        pathlib.Path(sys.executable).resolve().parent,
    ]):
        dlls_to_copy.extend(py_dir.glob("python3*.dll"))

    for dll in dlls_to_copy:
        dest = svc_dir / dll.name
        if not dest.exists():
            shutil.copy2(dll, dest)
            print(f"Copied {dll.name} -> {svc_dir}")

    save_config(host, port, env_file, exe_path=exe_path)
    try:
        win32serviceutil.InstallService(
            pythonClassString="rlm_tools_bsl._service_win.RlmWindowsService",
            serviceName=SERVICE_NAME,
            displayName=SERVICE_DISPLAY,
            description=SERVICE_DESC,
            startType=win32service.SERVICE_AUTO_START,
        )
        # Allow pythonservice.exe (system Python) to find rlm_tools_bsl at runtime
        # and locate the config file (LocalSystem has a different home dir)
        _set_service_environment(SERVICE_NAME, site_packages, str(CONFIG_FILE))
        print(f"Service '{SERVICE_NAME}' installed.")
        print("Start with: rlm-tools-bsl service start")
    except Exception as exc:
        print(f"Install error: {exc}")
        print("Make sure you are running as Administrator.")
        raise SystemExit(1)


def uninstall() -> None:
    try:
        win32serviceutil.StopService(SERVICE_NAME)
    except Exception:
        pass
    try:
        win32serviceutil.RemoveService(SERVICE_NAME)
        CONFIG_FILE.unlink(missing_ok=True)
        print(f"Service '{SERVICE_NAME}' removed.")
    except Exception as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)


def start() -> None:
    win32serviceutil.StartService(SERVICE_NAME)
    print("Service started.")


def stop() -> None:
    win32serviceutil.StopService(SERVICE_NAME)
    print("Service stopped.")


def status() -> None:
    try:
        s = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
        states = {
            win32service.SERVICE_RUNNING: "Running",
            win32service.SERVICE_STOPPED: "Stopped",
            win32service.SERVICE_START_PENDING: "Start Pending",
            win32service.SERVICE_STOP_PENDING: "Stop Pending",
        }
        print(f"Status: {states.get(s[1], str(s[1]))}")
    except Exception:
        print("Service not installed.")
