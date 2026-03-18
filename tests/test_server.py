import json
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

from rlm_tools_bsl.server import _rlm_start, _rlm_execute, _rlm_end


def test_full_rlm_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "example.py"), "w") as f:
            f.write("def hello():\n    return 'world'\n\ndef foo():\n    return 'bar'\n")

        start_result = _rlm_start(path=tmpdir, query="find all functions")
        result_data = json.loads(start_result)
        session_id = result_data["session_id"]
        assert session_id is not None
        assert "metadata" in result_data

        exec_result = _rlm_execute(
            session_id=session_id,
            code="files = glob_files('**/*.py')\nprint(f'Found {len(files)} Python files')"
        )
        exec_data = json.loads(exec_result)
        assert "Found 1 Python files" in exec_data["stdout"]

        exec_result2 = _rlm_execute(
            session_id=session_id,
            code="print(files)"
        )
        exec_data2 = json.loads(exec_result2)
        assert "example.py" in exec_data2["stdout"]

        end_result = _rlm_end(session_id=session_id)
        end_data = json.loads(end_result)
        assert end_data["success"] is True


def test_invalid_session():
    result = _rlm_execute(session_id="nonexistent", code="print('hi')")
    data = json.loads(result)
    assert "error" in data


def test_invalid_directory():
    result = _rlm_start(path="/nonexistent/path", query="test")
    data = json.loads(result)
    assert "error" in data


def test_resolve_mapped_drive_returns_unc():
    from rlm_tools_bsl.server import _resolve_mapped_drive

    if sys.platform != "win32":
        assert _resolve_mapped_drive("U:\\some\\path") is None
        return

    import winreg

    fake_sids = ["S-1-5-21-fake"]

    def fake_enum_key(hkey, index):
        if index < len(fake_sids):
            return fake_sids[index]
        raise OSError

    fake_key = MagicMock()

    def fake_open_key(hkey, sub_key):
        if sub_key == "S-1-5-21-fake\\Network\\U":
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=fake_key)
            cm.__exit__ = MagicMock(return_value=False)
            return cm
        raise OSError

    def fake_query(key, name):
        if key is fake_key and name == "RemotePath":
            return ("\\\\server\\share", winreg.REG_SZ)
        raise OSError

    with patch.object(winreg, "EnumKey", side_effect=fake_enum_key), \
         patch.object(winreg, "OpenKey", side_effect=fake_open_key), \
         patch.object(winreg, "QueryValueEx", side_effect=fake_query):
        result = _resolve_mapped_drive("U:\\ERP\\mainconf")
        assert result == "\\\\server\\share\\ERP\\mainconf"


def test_resolve_mapped_drive_no_mapping():
    from rlm_tools_bsl.server import _resolve_mapped_drive

    if sys.platform != "win32":
        return

    import winreg

    def fake_enum_key(hkey, index):
        raise OSError  # no SIDs

    with patch.object(winreg, "EnumKey", side_effect=fake_enum_key):
        result = _resolve_mapped_drive("Z:\\nonexistent")
        assert result is None


def test_resolve_mapped_drive_not_windows():
    from rlm_tools_bsl.server import _resolve_mapped_drive

    with patch("rlm_tools_bsl.server.os.name", "posix"):
        assert _resolve_mapped_drive("U:\\some\\path") is None


def test_invalid_directory_hint_inaccessible_drive():
    """Error should include UNC hint when drive root is inaccessible."""
    result = _rlm_start(path="Z:\\nonexistent\\path", query="test")
    data = json.loads(result)
    assert "error" in data
    if sys.platform == "win32" and not os.path.isdir("Z:\\"):
        assert "UNC" in data["error"] or "drive Z:" in data["error"]


def test_metadata_includes_file_types():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()
        open(os.path.join(tmpdir, "b.py"), "w").close()
        open(os.path.join(tmpdir, "c.txt"), "w").close()

        result = _rlm_start(path=tmpdir, query="test", include_metadata=True)
        data = json.loads(result)
        assert data["metadata"]["total_files"] == 3
        assert ".py" in data["metadata"]["file_types"]

        _rlm_end(data["session_id"])


def test_read_file_in_sandbox():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "data.txt"), "w") as f:
            f.write("important data")

        result = _rlm_start(path=tmpdir, query="read file")
        data = json.loads(result)
        session_id = data["session_id"]

        exec_result = _rlm_execute(
            session_id=session_id,
            code="content = read_file('data.txt')\nprint(content)"
        )
        exec_data = json.loads(exec_result)
        assert "important data" in exec_data["stdout"]
        assert exec_data["error"] is None

        _rlm_end(session_id)


def test_grep_in_sandbox():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "code.py"), "w") as f:
            f.write("class MyController:\n    def handle_error(self):\n        pass\n")

        result = _rlm_start(path=tmpdir, query="find controllers")
        data = json.loads(result)
        session_id = data["session_id"]

        exec_result = _rlm_execute(
            session_id=session_id,
            code="results = grep('class.*Controller')\nprint(len(results))"
        )
        exec_data = json.loads(exec_result)
        assert "1" in exec_data["stdout"]

        _rlm_end(session_id)


def test_skip_metadata_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()

        result = _rlm_start(path=tmpdir, query="test", include_metadata=False)
        data = json.loads(result)
        assert data["metadata"] == {}
        assert "session_id" in data

        _rlm_end(data["session_id"])


def test_new_helpers_in_sandbox():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("hello from a")
        with open(os.path.join(tmpdir, "b.txt"), "w") as f:
            f.write("hello from b")

        result = _rlm_start(path=tmpdir, query="test helpers")
        data = json.loads(result)
        session_id = data["session_id"]

        exec_result = _rlm_execute(
            session_id=session_id,
            code="result = read_files(['a.txt', 'b.txt'])\nfor k, v in sorted(result.items()):\n    print(f'{k}: {v}')"
        )
        exec_data = json.loads(exec_result)
        assert "a.txt: hello from a" in exec_data["stdout"]
        assert "b.txt: hello from b" in exec_data["stdout"]

        exec_result2 = _rlm_execute(
            session_id=session_id,
            code="print(grep_summary('hello'))"
        )
        exec_data2 = json.loads(exec_result2)
        assert "2 matches" in exec_data2["stdout"]

        exec_result3 = _rlm_execute(
            session_id=session_id,
            code="result = grep_read('hello')\nprint(result['summary'])"
        )
        exec_data3 = json.loads(exec_result3)
        assert "2 matches" in exec_data3["stdout"]

        _rlm_end(session_id)


def test_new_defaults():
    """Default effort=medium -> 25 execute calls, 15 llm calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()

        result = _rlm_start(path=tmpdir, query="test defaults")
        data = json.loads(result)
        assert data["limits"]["max_execute_calls"] == 25  # medium effort
        assert data["limits"]["max_llm_calls"] == 15  # medium effort
        assert data["limits"]["execution_timeout_seconds"] == 45

        _rlm_end(data["session_id"])


def test_full_detail_excludes_helper_functions_from_variables():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("hello")

        start = json.loads(_rlm_start(path=tmpdir, query="detail vars"))
        session_id = start["session_id"]

        result = json.loads(
            _rlm_execute(
                session_id=session_id,
                code="x = 123",
                detail_level="full",
            )
        )

        assert "x" in result["variables"]
        assert "read_files" not in result["variables"]
        assert "grep_summary" not in result["variables"]
        assert "grep_read" not in result["variables"]
        assert "find_module" not in result["variables"]
        assert "find_by_type" not in result["variables"]
        assert "extract_procedures" not in result["variables"]
        assert "safe_grep" not in result["variables"]
        assert "find_files" not in result["variables"]

        _rlm_end(session_id)


def test_extension_context_main_with_nearby_extension():
    """rlm_start returns extension_context with nearby extensions for main config."""
    import textwrap
    with tempfile.TemporaryDirectory() as parent:
        main_dir = os.path.join(parent, "main")
        ext_dir = os.path.join(parent, "ext")
        os.makedirs(main_dir)
        os.makedirs(ext_dir)

        # Main config
        with open(os.path.join(main_dir, "Configuration.xml"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
                    <Configuration uuid="00000000-0000-0000-0000-000000000001">
                        <Properties>
                            <Name>Основная</Name>
                            <NamePrefix/>
                        </Properties>
                    </Configuration>
                </MetaDataObject>
            """))

        # Extension
        with open(os.path.join(ext_dir, "Configuration.xml"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
                    <Configuration uuid="00000000-0000-0000-0000-000000000002">
                        <Properties>
                            <ObjectBelonging>Adopted</ObjectBelonging>
                            <Name>ТестРасш</Name>
                            <ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose>
                            <NamePrefix>мр_</NamePrefix>
                        </Properties>
                    </Configuration>
                </MetaDataObject>
            """))

        result = _rlm_start(path=main_dir, query="test ext context")
        data = json.loads(result)

        assert "extension_context" in data
        ec = data["extension_context"]
        assert ec["is_extension"] is False
        assert ec["config_role"] == "main"
        assert len(ec["nearby_extensions"]) == 1
        assert ec["nearby_extensions"][0]["name"] == "ТестРасш"
        assert ec["nearby_extensions"][0]["purpose"] == "AddOn"
        # warnings are at top level, not inside extension_context
        assert len(data["warnings"]) > 0

        _rlm_end(data["session_id"])


def test_extension_context_for_extension():
    """rlm_start for extension shows is_extension=True."""
    import textwrap
    with tempfile.TemporaryDirectory() as parent:
        ext_dir = os.path.join(parent, "myext")
        os.makedirs(ext_dir)

        with open(os.path.join(ext_dir, "Configuration.xml"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
                    <Configuration uuid="00000000-0000-0000-0000-000000000003">
                        <Properties>
                            <ObjectBelonging>Adopted</ObjectBelonging>
                            <Name>Расширение1</Name>
                            <ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>
                            <NamePrefix>р1_</NamePrefix>
                        </Properties>
                    </Configuration>
                </MetaDataObject>
            """))

        result = _rlm_start(path=ext_dir, query="test ext")
        data = json.loads(result)

        ec = data["extension_context"]
        assert ec["is_extension"] is True
        assert ec["config_role"] == "extension"
        assert ec["current_name"] == "Расширение1"
        assert ec["current_purpose"] == "Customization"
        assert ec["current_prefix"] == "р1_"

        _rlm_end(data["session_id"])


def test_config_format_returned():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "script.bsl"), "w").close()

        result = _rlm_start(path=tmpdir, query="test format")
        data = json.loads(result)
        assert "config_format" in data
        assert data["config_format"] in ("cf", "edt", "unknown")

        _rlm_end(data["session_id"])


def test_strategy_always_returned():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.bsl"), "w").close()

        result = _rlm_start(path=tmpdir, query="test strategy")
        data = json.loads(result)
        assert "strategy" in data
        assert "find_module" in data["strategy"]
        assert "CRITICAL" in data["strategy"]

        _rlm_end(data["session_id"])


def test_effort_levels():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()

        for effort, expected_exec in [("low", 10), ("medium", 25), ("high", 50), ("max", 100)]:
            result = _rlm_start(path=tmpdir, query="test effort", effort=effort)
            data = json.loads(result)
            assert data["limits"]["max_execute_calls"] == expected_exec, f"effort={effort}"
            _rlm_end(data["session_id"])


def test_bsl_helpers_in_sandbox():
    """BSL helpers should be available in sandbox when format_info is provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "CommonModules", "TestModule", "Ext"))
        with open(os.path.join(tmpdir, "CommonModules", "TestModule", "Ext", "Module.bsl"), "w", encoding="utf-8") as f:
            f.write("Процедура Тест() Экспорт\nКонецПроцедуры\n")
        with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
            f.write("<Configuration/>")

        start = json.loads(_rlm_start(path=tmpdir, query="test bsl helpers"))
        session_id = start["session_id"]
        assert start["config_format"] == "cf"

        # Test find_module
        result = json.loads(_rlm_execute(
            session_id=session_id,
            code="modules = find_module('TestModule')\nprint(len(modules))"
        ))
        assert "1" in result["stdout"]
        assert result["error"] is None

        # Test extract_procedures
        result2 = json.loads(_rlm_execute(
            session_id=session_id,
            code="procs = extract_procedures(modules[0]['path'])\nprint(procs[0]['name'])"
        ))
        assert "Тест" in result2["stdout"]

        _rlm_end(session_id)


def test_override_effort_limits():
    """Manual max_llm_calls and max_execute_calls override effort defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()

        result = _rlm_start(
            path=tmpdir,
            query="test override",
            effort="low",
            max_execute_calls=99,
            max_llm_calls=77,
        )
        data = json.loads(result)
        assert data["limits"]["max_execute_calls"] == 99
        assert data["limits"]["max_llm_calls"] == 77

        _rlm_end(data["session_id"])


# ---------------------------------------------------------------------------
# Transport / main() tests
# ---------------------------------------------------------------------------

def test_main_default_stdio():
    """main() without args calls mcp.run(transport='stdio')."""
    from rlm_tools_bsl import server

    with patch.object(server.mcp, "run") as mock_run, \
         patch.object(sys, "argv", ["rlm-tools-bsl"]):
        server.main()
        mock_run.assert_called_once_with(transport="stdio")


def test_main_streamable_http_arg():
    """--transport streamable-http sets transport and updates settings."""
    from rlm_tools_bsl import server

    original_host = server.mcp.settings.host
    original_port = server.mcp.settings.port
    try:
        with patch.object(server.mcp, "run") as mock_run, \
             patch.object(sys, "argv", ["rlm-tools-bsl", "--transport", "streamable-http"]):
            server.main()
            mock_run.assert_called_once_with(transport="streamable-http")
            assert server.mcp.settings.host == "127.0.0.1"
            assert server.mcp.settings.port == 9000
    finally:
        server.mcp.settings.host = original_host
        server.mcp.settings.port = original_port


def test_main_custom_port():
    """--port overrides default port in mcp.settings."""
    from rlm_tools_bsl import server

    original_port = server.mcp.settings.port
    try:
        with patch.object(server.mcp, "run") as mock_run, \
             patch.object(sys, "argv", [
                 "rlm-tools-bsl", "--transport", "streamable-http", "--port", "3000"
             ]):
            server.main()
            mock_run.assert_called_once_with(transport="streamable-http")
            assert server.mcp.settings.port == 3000
    finally:
        server.mcp.settings.port = original_port


def test_main_env_transport():
    """RLM_TRANSPORT env var is used as fallback when no CLI arg given."""
    from rlm_tools_bsl import server

    original_host = server.mcp.settings.host
    original_port = server.mcp.settings.port
    try:
        with patch.object(server.mcp, "run") as mock_run, \
             patch.object(sys, "argv", ["rlm-tools-bsl"]), \
             patch.dict(os.environ, {"RLM_TRANSPORT": "streamable-http"}):
            server.main()
            mock_run.assert_called_once_with(transport="streamable-http")
    finally:
        server.mcp.settings.host = original_host
        server.mcp.settings.port = original_port


def test_main_stdio_does_not_change_settings():
    """When transport is stdio, mcp.settings.host/port are NOT modified."""
    from rlm_tools_bsl import server

    original_host = server.mcp.settings.host
    original_port = server.mcp.settings.port
    try:
        with patch.object(server.mcp, "run") as mock_run, \
             patch.object(sys, "argv", ["rlm-tools-bsl"]):
            server.main()
            mock_run.assert_called_once_with(transport="stdio")
            assert server.mcp.settings.host == original_host
            assert server.mcp.settings.port == original_port
    finally:
        server.mcp.settings.host = original_host
        server.mcp.settings.port = original_port


def test_sandboxes_concurrent_access():
    """Concurrent create/end must not crash with _sandboxes_lock."""
    import threading

    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()

        errors = []

        def create_and_end():
            try:
                result = _rlm_start(path=tmpdir, query="concurrent test")
                data = json.loads(result)
                sid = data["session_id"]
                _rlm_execute(session_id=sid, code="print(1+1)")
                _rlm_end(session_id=sid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_and_end) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent access errors: {errors}"


def test_streamable_http_server_starts():
    """Integration: streamable-http server starts and responds to MCP initialize."""
    import socket
    import subprocess
    import time

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [sys.executable, "-m", "rlm_tools_bsl",
         "--transport", "streamable-http", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for server to start
        import httpx
        client = httpx.Client()
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        initialize_request = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
            "id": 1,
        }

        # Retry a few times while server starts up
        response = None
        for _ in range(20):
            try:
                response = client.post(
                    mcp_url,
                    json=initialize_request,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    timeout=2,
                )
                break
            except httpx.ConnectError:
                time.sleep(0.5)

        assert response is not None, "Server did not start in time"
        assert response.status_code == 200
        assert len(response.content) > 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)
