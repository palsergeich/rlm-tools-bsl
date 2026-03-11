import tempfile
from rlm_tools_bsl.sandbox import Sandbox


def test_execute_simple_code():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=10_000)
        result = sandbox.execute("x = 2 + 2\nprint(x)")
        assert result.stdout.strip() == "4"
        assert result.error is None


def test_variables_persist_between_executions():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=10_000)
        sandbox.execute("my_var = 42")
        result = sandbox.execute("print(my_var)")
        assert result.stdout.strip() == "42"


def test_output_truncated():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=50)
        result = sandbox.execute("print('a' * 200)")
        assert len(result.stdout) <= 80  # 50 + truncation message


def test_blocked_imports():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=10_000)
        result = sandbox.execute("import subprocess")
        assert result.error is not None


def test_no_write_access():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=10_000)
        result = sandbox.execute(f"open('{tmpdir}/evil.txt', 'w').write('hack')")
        assert result.error is not None


def test_list_variables():
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Sandbox(base_path=tmpdir, max_output_chars=10_000)
        sandbox.execute("foo = 1\nbar = 'hello'")
        variables = sandbox.list_variables()
        assert "foo" in variables
        assert "bar" in variables
