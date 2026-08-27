"""
Basic tests for the tool layer. Run with: pytest tests/
Focus is on correctness AND safety (sandbox escape attempts must fail).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.code_exec import CodeExecutionTool
from tools.file_ops import FileOpsTool


def test_write_and_read_file():
    tool = FileOpsTool()

    result = tool.run(operation="write", path="test.txt", content="hello world")
    assert "Wrote" in result

    content = tool.run(operation="read", path="test.txt")
    assert content == "hello world"


def test_read_nonexistent_file():
    tool = FileOpsTool()
    result = tool.run(operation="read", path="does_not_exist.txt")
    assert "not found" in result.lower()


def test_sandbox_escape_blocked():
    tool = FileOpsTool()
    result = tool.run(operation="write", path="../../etc/passwd", content="malicious")
    assert "escapes the sandbox" in result or "Error" in result


def test_list_files():
    tool = FileOpsTool()
    tool.run(operation="write", path="listed.txt", content="x")
    result = tool.run(operation="list")
    assert "listed.txt" in result


def test_unknown_file_operation():
    tool = FileOpsTool()
    result = tool.run(operation="delete", path="test.txt")
    assert "Unknown operation" in result


def test_code_execution_basic():
    tool = CodeExecutionTool()
    result = tool.run(code="print(2 + 2)")
    assert result.strip() == "4"


def test_code_execution_timeout():
    tool = CodeExecutionTool()
    result = tool.run(code="import time; time.sleep(30)")
    assert "timed out" in result.lower()


def test_code_execution_cleans_up_temp_script_after_timeout():
    """A timeout must not leak the temp .py script into the sandbox forever
    — confirmed live as a real bug: cleanup previously only ran on the
    success path, so every timed-out call left an orphaned tmp*.py file
    behind (a real QA sweep's sandbox listing turned up ~19 of them)."""
    from config import config

    tool = CodeExecutionTool()
    before = set(Path(config.sandbox_dir).glob("tmp*.py"))
    tool.run(code="import time; time.sleep(30)")
    after = set(Path(config.sandbox_dir).glob("tmp*.py"))
    assert after == before


def test_code_execution_captures_errors():
    tool = CodeExecutionTool()
    result = tool.run(code="1 / 0")
    assert "ZeroDivisionError" in result


def test_code_execution_no_print_gives_actionable_hint_not_bare_silence():
    # Confirmed live as a real bug: asked to compute "347*892-1500", the
    # model wrote code that never called print(), got an unhelpful empty
    # result back three times in a row, then fabricated a confident but
    # wrong final answer instead of admitting the tool gave it nothing. A
    # concrete hint here (instead of a bare "no output") gives it something
    # to act on other than guessing.
    tool = CodeExecutionTool()
    result = tool.run(code="x = 2 + 2")
    assert "print(" in result.lower()