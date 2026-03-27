"""Tests for CLI commands (rlm-bsl-index index build/update/info/drop)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest


@pytest.fixture()
def cli_bsl_project(tmp_path, monkeypatch):
    """Create a minimal BSL project and set RLM_INDEX_DIR."""
    # CF-format structure
    mod_dir = tmp_path / "CommonModules" / "TestModule" / "Ext"
    mod_dir.mkdir(parents=True)
    (mod_dir / "Module.bsl").write_text(
        textwrap.dedent("""\
            Процедура ТестоваяПроцедура() Экспорт
                Возврат;
            КонецПроцедуры

            Функция ТестоваяФункция(Параметр)
                Возврат Параметр;
            КонецФункции
        """),
        encoding="utf-8-sig",
    )
    idx_dir = tmp_path / "_index"
    idx_dir.mkdir()
    monkeypatch.setenv("RLM_INDEX_DIR", str(idx_dir))
    return tmp_path, idx_dir


def _run_cli(*args: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run CLI command and return result."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "rlm_tools_bsl.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class TestCliBuild:
    def test_cli_build(self, cli_bsl_project):
        project_path, idx_dir = cli_bsl_project
        result = _run_cli(
            "index",
            "build",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        assert result.returncode == 0
        assert "Индекс построен" in result.stdout or "methods" in result.stdout.lower()
        # DB file should exist
        db_files = list(idx_dir.rglob("method_index.db"))
        assert len(db_files) == 1

    def test_cli_build_no_calls(self, cli_bsl_project):
        project_path, idx_dir = cli_bsl_project
        result = _run_cli(
            "index",
            "build",
            "--no-calls",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        assert result.returncode == 0
        db_files = list(idx_dir.rglob("method_index.db"))
        assert len(db_files) == 1


class TestCliUpdate:
    def test_cli_update(self, cli_bsl_project):
        project_path, idx_dir = cli_bsl_project
        # First build
        _run_cli(
            "index",
            "build",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        # Then update
        result = _run_cli(
            "index",
            "update",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        assert result.returncode == 0


class TestCliInfo:
    def test_cli_info(self, cli_bsl_project):
        project_path, idx_dir = cli_bsl_project
        # Build first
        _run_cli(
            "index",
            "build",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        result = _run_cli(
            "index",
            "info",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        assert result.returncode == 0
        # Should contain stats
        out = result.stdout.lower()
        assert "модул" in out or "метод" in out or "module" in out or "method" in out


class TestCliDrop:
    def test_cli_drop(self, cli_bsl_project):
        project_path, idx_dir = cli_bsl_project
        # Build first
        _run_cli(
            "index",
            "build",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        db_files = list(idx_dir.rglob("method_index.db"))
        assert len(db_files) == 1
        # Drop
        result = _run_cli(
            "index",
            "drop",
            str(project_path),
            env_override={"RLM_INDEX_DIR": str(idx_dir)},
        )
        assert result.returncode == 0
        db_files = list(idx_dir.rglob("method_index.db"))
        assert len(db_files) == 0
