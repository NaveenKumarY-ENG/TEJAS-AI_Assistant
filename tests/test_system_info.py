"""
Tests for tools/system_info.py. Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.system_info import SystemInfoTool

tool = SystemInfoTool()


def test_returns_os_cpu_and_disk_info():
    result = tool.run()
    assert "OS:" in result
    assert "Machine:" in result
    assert "Processor:" in result
    assert "Python:" in result
    assert "Disk:" in result


def test_disk_usage_is_real_and_internally_consistent():
    result = tool.run()
    line = next(l for l in result.splitlines() if l.startswith("Disk:"))
    used, total = (int(n) for n in __import__("re").findall(r"(\d+) GB", line)[:2])
    assert 0 <= used <= total


def test_disk_usage_error_is_reported_not_raised():
    with patch("tools.system_info.shutil.disk_usage", side_effect=OSError("no such volume")):
        result = tool.run()
    assert "Error getting system info" in result
