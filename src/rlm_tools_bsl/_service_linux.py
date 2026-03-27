"""Linux systemd --user service management for rlm-tools-bsl."""

import shutil
import subprocess
from pathlib import Path

from rlm_tools_bsl.service import CONFIG_FILE, save_config

SERVICE_NAME = "rlm-tools-bsl"


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("rlm-tools-bsl")
    except Exception:
        return "?"


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _exe_path() -> str:
    return shutil.which("rlm-tools-bsl") or "rlm-tools-bsl"


def install(host: str, port: int, env_file: str | None) -> None:
    save_config(host, port, env_file)
    unit_dir = _unit_path().parent
    unit_dir.mkdir(parents=True, exist_ok=True)

    env_line = f"EnvironmentFile=-{env_file}" if env_file else "# EnvironmentFile not configured"
    exe = _exe_path()
    unit = (
        "[Unit]\n"
        f"Description=RLM Tools BSL (MCP HTTP Server) v{_get_version()}\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exe} --transport streamable-http --host {host} --port {port}\n"
        f"{env_line}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    _unit_path().write_text(unit, encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)

    print(f"Служба '{SERVICE_NAME}' установлена.")
    print(f"Unit-файл: {_unit_path()}")
    print("Для автозапуска без входа в систему выполните:")
    print(f"  loginctl enable-linger {Path.home().name}")
    print("Запуск: rlm-tools-bsl service start")


def uninstall() -> None:
    try:
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
    except Exception:
        pass
    _unit_path().unlink(missing_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"Служба '{SERVICE_NAME}' удалена.")


def start() -> None:
    subprocess.run(["systemctl", "--user", "start", SERVICE_NAME], check=True)
    print("Служба запущена.")


def stop() -> None:
    subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME], check=True)
    print("Служба остановлена.")


def status() -> None:
    result = subprocess.run(
        ["systemctl", "--user", "status", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    print(result.stdout or result.stderr)
