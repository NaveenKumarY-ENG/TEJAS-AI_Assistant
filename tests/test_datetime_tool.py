"""
Tests for tools/datetime_tool.py. Run with: pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.datetime_tool import DateTimeTool

tool = DateTimeTool()


def test_no_timezone_returns_local_time():
    result = tool.run()
    assert "system local time" in result


def test_valid_timezone_returns_that_timezone():
    result = tool.run(timezone="Asia/Kolkata")
    assert "Asia/Kolkata" in result
    assert "system local time" not in result


def test_another_valid_timezone():
    result = tool.run(timezone="America/New_York")
    assert "America/New_York" in result


def test_invalid_timezone_falls_back_to_local_instead_of_erroring():
    """Regression guard for a real failure mode: small local models sometimes
    echo descriptive schema text or an invalid city name as the argument
    instead of omitting it. An error here would give the model license to
    fabricate a plausible-sounding date instead of just answering with what
    it actually knows (local time)."""
    result = tool.run(timezone="not a real timezone")
    assert "system local time" in result
    assert "Error" not in result


def test_empty_string_timezone_falls_back_to_local():
    result = tool.run(timezone="")
    assert "system local time" in result


def test_result_includes_a_weekday_name_and_readable_date():
    result = tool.run()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert any(day in result for day in weekdays)
