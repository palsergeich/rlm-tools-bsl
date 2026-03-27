"""Service management for rlm-tools-bsl HTTP server (Windows SC / Linux systemd)."""

import json
import os
import pathlib
import sys

CONFIG_DIR = pathlib.Path.home() / ".config" / "rlm-tools-bsl"
CONFIG_FILE = CONFIG_DIR / "service.json"


def _config_path() -> pathlib.Path:
    """Return the config file path.

    On Windows, the service runs as LocalSystem whose home dir differs from
    the installing user.  The install step writes RLM_CONFIG_FILE into the
    service's registry Environment so load_config() can find it at runtime.
    """
    override = os.environ.get("RLM_CONFIG_FILE")
    if override:
        return pathlib.Path(override)
    return CONFIG_FILE


def save_config(host: str, port: int, env_file: str | None, exe_path: str | None = None) -> None:
    cfg = _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"host": host, "port": port, "env_file": env_file, "exe_path": exe_path}, indent=2),
        encoding="utf-8",
    )


def load_config() -> dict:
    cfg = _config_path()
    if not cfg.exists():
        return {"host": "127.0.0.1", "port": 9000, "env_file": None}
    return json.loads(cfg.read_text(encoding="utf-8"))


def handle_service_command(args) -> None:
    if sys.platform == "win32":
        try:
            from rlm_tools_bsl._service_win import (  # type: ignore[import]
                install,
                uninstall,
                start,
                stop,
                status,
            )
        except ImportError:
            print(
                "Ошибка: для управления службой на Windows требуется pywin32.\n"
                "Установите: uv tool install rlm-tools-bsl --extra service\n"
                "  или: pip install pywin32"
            )
            raise SystemExit(1)
    else:
        from rlm_tools_bsl._service_linux import (
            install,
            uninstall,
            start,
            stop,
            status,
        )

    action = args.service_action
    if action == "install":
        install(host=args.host, port=args.port, env_file=args.env)
    elif action == "uninstall":
        uninstall()
    elif action == "start":
        start()
    elif action == "stop":
        stop()
    elif action == "status":
        status()
    else:
        print("Использование: rlm-tools-bsl service {install|start|stop|status|uninstall}")
        raise SystemExit(1)
