"""
Tests for the sandboxed Python execution tool (tools/code_exec.py) — the
tool models actually reach for on "calculate X" / "run this snippet"
requests. Run with: pytest tests/
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config
from tools.code_exec import CodeExecutionTool, TIMEOUT_SECONDS

tool = CodeExecutionTool()


def test_temp_script_is_written_outside_the_reload_watched_sandbox():
    """Regression test for a real, live-reproduced bug: the temp script
    used to be written into config.sandbox_dir, which sits inside the
    project root `uvicorn server:app --reload` (the documented dev command)
    watches for .py changes. Every execute_python call reloaded the whole
    server mid-response, closing the WebSocket with code 1012 ("service
    restart") on literally every call that reached this tool. The script
    must land outside both the sandbox and the system temp dir being
    (implausibly) the same path as the sandbox."""
    sandbox = Path(config.sandbox_dir).resolve()
    system_temp = Path(tempfile.gettempdir()).resolve()
    assert sandbox != system_temp, "test assumption violated: sandbox_dir IS the system temp dir"

    tool.run(code="print(1)")
    leaked_into_sandbox = set(sandbox.glob("tmp*.py"))
    assert leaked_into_sandbox == set(), "a temp script landed inside the reload-watched sandbox directory"


def test_basic_arithmetic():
    assert tool.run(code="print(4739 * 8256)") == "39125184"


def test_float_division():
    assert tool.run(code="print(10 / 3)") == "3.3333333333333335"


def test_negative_numbers_and_operator_precedence():
    assert tool.run(code="print((-45 + 12) * 3 - 100 / 4)") == "-124.0"


def test_modulo_and_floor_division():
    assert tool.run(code="print(17 % 5)") == "2"
    assert tool.run(code="print(-7 // 2)") == "-4"  # Python floor-divides toward negative infinity


def test_large_integers_stay_exact():
    """Python's arbitrary-precision ints — no float rounding creeping in on
    a calculation a naive float-based calculator would get wrong."""
    assert tool.run(code="print(99999999999999999999 * 99999999999999999999)") == (
        "9999999999999999999800000000000000000001"
    )


def test_large_exponent():
    assert tool.run(code="print(2 ** 100)") == "1267650600228229401496703205376"


def test_factorial():
    assert tool.run(code="import math; print(math.factorial(20))") == "2432902008176640000"


def test_complex_number_result():
    assert tool.run(code="import cmath; print(cmath.sqrt(-16))") == "4j"


def test_division_by_zero_surfaces_the_real_error():
    """A wrong-but-confident number is worse than a clear error — the
    model needs to see this failed, not silently get nothing back."""
    result = tool.run(code="print(10 / 0)")
    assert "ZeroDivisionError" in result


def test_syntax_error_surfaces_clearly():
    result = tool.run(code="print(5 +)")
    assert "SyntaxError" in result


def test_type_error_surfaces_clearly():
    result = tool.run(code='print("5" + 5)')
    assert "TypeError" in result


def test_no_print_gives_an_actionable_nudge_not_silence():
    """Regression guard for a real bug found live: a model asked to
    compute something wrote code that never called print(), got empty
    output three times, then confidently stated a wrong number from its
    own head rather than admitting the tool gave it nothing."""
    result = tool.run(code="x = 5 + 5")
    assert "didn't print" in result


def test_infinite_loop_times_out_instead_of_hanging():
    result = tool.run(code="while True: pass")
    assert f"timed out after {TIMEOUT_SECONDS}s" in result


def test_temp_script_is_cleaned_up_even_on_timeout():
    """Regression guard for a real bug found live: cleanup previously only
    ran on the success path, so a timeout (or any other exception from
    subprocess.run) leaked the temp script forever — a real QA sweep's
    sandbox turned up ~19 orphaned tmp*.py files this way."""
    from config import config

    before = set(Path(config.sandbox_dir).glob("tmp*.py"))
    tool.run(code="while True: pass")
    after = set(Path(config.sandbox_dir).glob("tmp*.py"))
    assert after - before == set()


def test_non_math_code_still_works():
    """The tool is general-purpose code execution, not a calculator
    specifically — a plain string operation should work identically."""
    assert tool.run(code='print("abc" + "def")') == "abcdef"
