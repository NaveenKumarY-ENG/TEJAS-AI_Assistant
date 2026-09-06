"""
Tests for tools/file_ops.py — the sandbox confinement in particular, since
that's the actual security boundary (the assistant must never read/write/
list anything outside config.sandbox_dir). Run with: pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config
from tools.file_ops import FileOpsTool, _safe_path

tool = FileOpsTool()


def test_write_then_read_round_trip():
    tool.run(operation="write", path="qa_test.txt", content="hello sandbox")
    try:
        assert tool.run(operation="read", path="qa_test.txt") == "hello sandbox"
    finally:
        (Path(config.sandbox_dir) / "qa_test.txt").unlink(missing_ok=True)


def test_write_creates_parent_directories():
    tool.run(operation="write", path="qa_sub/qa_nested.txt", content="nested")
    try:
        assert tool.run(operation="read", path="qa_sub/qa_nested.txt") == "nested"
    finally:
        (Path(config.sandbox_dir) / "qa_sub" / "qa_nested.txt").unlink(missing_ok=True)
        (Path(config.sandbox_dir) / "qa_sub").rmdir()


def test_read_nonexistent_file_reports_not_found_not_an_exception():
    result = tool.run(operation="read", path="qa_does_not_exist.txt")
    assert "not found" in result.lower()


def test_write_requires_content():
    result = tool.run(operation="write", path="qa_test.txt")
    assert "content" in result.lower()
    assert "required" in result.lower()


def test_list_defaults_to_sandbox_root():
    result = tool.run(operation="list")
    assert "Error" not in result


def test_list_nonexistent_directory_reports_not_found():
    result = tool.run(operation="list", path="qa_no_such_dir")
    assert "not found" in result.lower()


def test_list_empty_directory_says_so():
    (Path(config.sandbox_dir) / "qa_empty_dir").mkdir(exist_ok=True)
    try:
        assert tool.run(operation="list", path="qa_empty_dir") == "(empty directory)"
    finally:
        (Path(config.sandbox_dir) / "qa_empty_dir").rmdir()


def test_unknown_operation_reports_clearly():
    result = tool.run(operation="delete", path="qa_test.txt")
    assert "Unknown operation" in result


# ---------------------------------------------------------------------
# Sandbox escape attempts — the actual security boundary this tool exists
# to enforce. Every one of these must be refused, never silently resolved
# to somewhere outside config.sandbox_dir.
# ---------------------------------------------------------------------


def test_path_traversal_with_dotdot_is_refused():
    try:
        _safe_path("../../etc/passwd")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "escapes the sandbox" in str(e)


def test_deeply_nested_path_traversal_is_refused():
    try:
        _safe_path("a/b/../../../../../../windows/system32")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_absolute_path_outside_sandbox_is_refused():
    try:
        _safe_path("C:\\Windows\\System32\\config\\SAM")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_read_via_traversal_reports_a_clean_error_not_real_file_contents():
    """End-to-end: even if a caller (a model) tries a traversal path
    through the actual tool interface (not just _safe_path directly), it
    must come back as a refusal, never real file contents from outside the
    sandbox."""
    result = tool.run(operation="read", path="../../../../../../Windows/win.ini")
    assert "escapes the sandbox" in result
    assert "[" not in result  # not accidentally leaking win.ini's real contents


def test_sandbox_root_itself_is_allowed():
    """The boundary check must not be so strict it refuses the sandbox
    directory itself (target == sandbox is a valid, common case for 'list
    everything')."""
    assert _safe_path(".") == Path(config.sandbox_dir).resolve()
