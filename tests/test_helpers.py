import os
import tempfile
from rlm_tools_bsl.helpers import make_helpers


def test_read_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "hello.txt")
        with open(test_file, "w") as f:
            f.write("hello world")

        helpers, _ = make_helpers(tmpdir)
        content = helpers["read_file"]("hello.txt")
        assert content == "hello world"


def test_read_file_blocks_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        helpers, _ = make_helpers(tmpdir)
        try:
            helpers["read_file"]("../../etc/passwd")
            assert False, "Should have raised"
        except PermissionError:
            pass


def test_grep():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "code.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    pass\ndef world():\n    pass\n")

        helpers, _ = make_helpers(tmpdir)
        results = helpers["grep"]("def.*hello")
        assert len(results) > 0
        assert "hello" in results[0]["text"]


def test_glob_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, "a.py"), "w").close()
        open(os.path.join(tmpdir, "b.py"), "w").close()
        open(os.path.join(tmpdir, "c.txt"), "w").close()

        helpers, _ = make_helpers(tmpdir)
        py_files = helpers["glob_files"]("**/*.py")
        assert len(py_files) == 2


def test_glob_files_dir_pattern_hint():
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = os.path.join(tmpdir, "MyModule")
        os.makedirs(subdir)
        open(os.path.join(subdir, "Module.bsl"), "w").close()

        helpers, _ = make_helpers(tmpdir)
        # Pattern matches a directory, not files — should return hint
        result = helpers["glob_files"]("My*")
        assert len(result) == 1
        assert result[0].startswith("[hint:")
        assert "1 directories" in result[0]
        assert "My*" in result[0]


def test_tree():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "src"))
        open(os.path.join(tmpdir, "src", "main.py"), "w").close()

        helpers, _ = make_helpers(tmpdir)
        output = helpers["tree"]()
        assert "src" in output
        assert "main.py" in output


def test_read_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("content a")
        with open(os.path.join(tmpdir, "b.txt"), "w") as f:
            f.write("content b")

        helpers, _ = make_helpers(tmpdir)
        result = helpers["read_files"](["a.txt", "b.txt"])
        assert result["a.txt"] == "content a"
        assert result["b.txt"] == "content b"


def test_read_files_handles_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "exists.txt"), "w") as f:
            f.write("hello")

        helpers, _ = make_helpers(tmpdir)
        result = helpers["read_files"](["exists.txt", "missing.txt"])
        assert result["exists.txt"] == "hello"
        assert "[error:" in result["missing.txt"]


def test_read_file_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "cached.txt")
        with open(test_file, "w") as f:
            f.write("original")

        helpers, _ = make_helpers(tmpdir)
        first = helpers["read_file"]("cached.txt")
        assert first == "original"

        with open(test_file, "w") as f:
            f.write("modified")

        second = helpers["read_file"]("cached.txt")
        assert second == "original"  # cached, not re-read


def test_grep_summary():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "code.py"), "w") as f:
            f.write("def hello():\n    pass\ndef world():\n    pass\n")

        helpers, _ = make_helpers(tmpdir)
        output = helpers["grep_summary"]("def")
        assert "2 matches in 1 files:" in output
        assert "code.py" in output
        assert "L1:" in output
        assert "L3:" in output


def test_grep_summary_no_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "code.py"), "w") as f:
            f.write("hello world\n")

        helpers, _ = make_helpers(tmpdir)
        output = helpers["grep_summary"]("zzz_nonexistent")
        assert output == "No matches found."


def test_grep_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "reducer.swift"), "w") as f:
            f.write("struct MyReducer: Reducer {\n    var body: some ReducerOf<Self> {\n    }\n}\n")
        with open(os.path.join(tmpdir, "model.swift"), "w") as f:
            f.write("struct Model {\n    var name: String\n}\n")

        helpers, _ = make_helpers(tmpdir)
        result = helpers["grep_read"]("Reducer")
        assert "reducer.swift" in result["matches"]
        assert "model.swift" not in result["matches"]
        assert "struct MyReducer" in result["files"]["reducer.swift"]
        assert "matches in 1 files" in result["summary"]


def test_grep_read_with_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "code.py"), "w") as f:
            f.write("line1\nline2\ntarget\nline4\nline5\n")

        helpers, _ = make_helpers(tmpdir)
        result = helpers["grep_read"]("target", context_lines=1)
        content = result["files"]["code.py"]
        assert "L2:" in content
        assert "L3:" in content
        assert "L4:" in content
        assert "L1:" not in content


def test_grep_read_max_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(5):
            with open(os.path.join(tmpdir, f"file{i}.py"), "w") as f:
                f.write(f"def func{i}():\n    pass\n")

        helpers, _ = make_helpers(tmpdir)
        result = helpers["grep_read"]("def", max_files=2)
        assert len(result["files"]) == 2
        assert "more" in result["summary"]


def test_find_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "CommonModules", "MyModule", "Ext"))
        with open(os.path.join(tmpdir, "CommonModules", "MyModule", "Ext", "Module.bsl"), "w") as f:
            f.write("// code")
        with open(os.path.join(tmpdir, "script.py"), "w") as f:
            f.write("pass")

        helpers, _ = make_helpers(tmpdir)
        results = helpers["find_files"]("Module.bsl")
        assert len(results) >= 1
        assert any("Module.bsl" in r for r in results)

        results2 = helpers["find_files"]("script")
        assert len(results2) >= 1

        results3 = helpers["find_files"]("nonexistent_xyz")
        assert len(results3) == 0


def test_find_files_case_insensitive():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "MyFile.txt"), "w") as f:
            f.write("test")

        helpers, _ = make_helpers(tmpdir)
        results = helpers["find_files"]("myfile")
        assert len(results) == 1


def test_read_file_utf8_sig():
    """Test that utf-8-sig BOM is handled correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "bom.txt")
        with open(test_file, "wb") as f:
            f.write(b"\xef\xbb\xbfhello with BOM")

        helpers, _ = make_helpers(tmpdir)
        content = helpers["read_file"]("bom.txt")
        assert content == "hello with BOM"
        assert not content.startswith("\ufeff")
