"""
Sandboxed Python code execution.
Runs in a subprocess with a timeout and a working directory scoped to the
sandbox — process isolation and a timeout only, not OS-level sandboxing:
the subprocess still has the full filesystem/network permissions of the
user running this app (nothing stops `open("C:/Users/.../secrets.txt")` or
an outbound request from an executed snippet). Fine for this app's actual
threat model (a single trusted local user asking their own assistant to run
a snippet, not arbitrary untrusted code from a third party); the timeout
and process boundary just mean a bad snippet can't hang or take down the
assistant process itself.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from config import config
from tools.base import Tool

TIMEOUT_SECONDS = 10


class CodeExecutionTool(Tool):
    name = "execute_python"
    description = (
        f"Run a short Python snippet for calculations or quick scripts. Only stdout is "
        f"returned — the code MUST call print(...) on whatever value you need to see, or "
        f"you will get nothing back. Isolated subprocess, {TIMEOUT_SECONDS}s timeout."
    )
    input_schema = {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python code to execute"}},
        "required": ["code"],
    }

    def run(self, code: str) -> str:
        script_path = None
        try:
            # Deliberately NOT dir=config.sandbox_dir: the default `dev`
            # workflow runs `uvicorn server:app --reload` (see README), and
            # that sandbox directory sits inside the project root --reload
            # watches for .py changes. Every execute_python call used to
            # write its throwaway script there, which uvicorn's watcher
            # picked up as a real code change and reloaded the whole
            # server mid-response -- confirmed live: the WebSocket closed
            # with code 1012 ("service restart") on literally every call
            # that reached this tool, and since the process was torn down
            # before this function's own `finally` cleanup could run, the
            # temp script was left behind every time too (a live sweep
            # found 34 leaked tmp*.py files in the sandbox from this alone).
            # The system's default temp directory is outside any watched
            # tree, so the script itself lives there; the executed code's
            # *own* cwd (below) is still the real sandbox, unchanged.
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                script_path = f.name

            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=config.sandbox_dir,
            )

            output = result.stdout.strip()
            error = result.stderr.strip()
            if error:
                return f"stdout:\n{output}\n\nstderr:\n{error}"
            # Confirmed live: a model asked to compute "347*892-1500" wrote code
            # that never called print(), got this back empty three times, then
            # confidently stated a wrong number from its own head rather than
            # admitting the tool gave it nothing — an explicit nudge here (not
            # just the tool description) gives it a concrete next step instead
            # of silence to fill in with a guess.
            return output if output else (
                "(no output — your code didn't print() anything. Add a print() "
                "call for the value you need and try again.)"
            )
        except subprocess.TimeoutExpired:
            return f"Execution timed out after {TIMEOUT_SECONDS}s."
        except Exception as e:
            return f"Error executing code: {e}"
        finally:
            # Was previously only deleted on the success path — a timeout or
            # any other exception raised by subprocess.run() skipped this
            # entirely, leaking the temp script into the sandbox forever.
            # Confirmed live: a real QA sweep's sandbox listing turned up ~19
            # orphaned tmp*.py files accumulated exactly this way.
            if script_path:
                Path(script_path).unlink(missing_ok=True)