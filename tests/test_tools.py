"""
Basic tests for the tool layer. Run with: pytest tests/
Focus is on correctness AND safety (sandbox escape attempts must fail).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.code_exec import CodeExecutionTool
from tools.file_ops import ListFilesTool, ReadFileTool, WriteFileTool


def test_write_and_read_file():
    write_tool = WriteFileTool()
    read_tool = ReadFileTool()

    result = write_tool.run(path="test.txt", content="hello world")
    assert "Wrote" in result

    content = read_tool.run(path="test.txt")
    assert content == "hello world"


def test_read_nonexistent_file():
    read_tool = ReadFileTool()
    result = read_tool.run(path="does_not_exist.txt")
    assert "not found" in result.lower()


def test_sandbox_escape_blocked():
    write_tool = WriteFileTool()
    result = write_tool.run(path="../../etc/passwd", content="malicious")
    assert "escapes the sandbox" in result or "Error" in result


def test_list_files():
    write_tool = WriteFileTool()
    write_tool.run(path="listed.txt", content="x")
    list_tool = ListFilesTool()
    result = list_tool.run()
    assert "listed.txt" in result


def test_code_execution_basic():
    tool = CodeExecutionTool()
    result = tool.run(code="print(2 + 2)")
    assert result.strip() == "4"


def test_code_execution_timeout():
    tool = CodeExecutionTool()
    result = tool.run(code="import time; time.sleep(30)")
    assert "timed out" in result.lower()


def test_code_execution_captures_errors():
    tool = CodeExecutionTool()
    result = tool.run(code="1 / 0")
    assert "ZeroDivisionError" in result