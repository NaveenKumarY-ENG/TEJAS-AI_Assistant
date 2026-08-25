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
        f"Run a short Python snippet (stdout/stderr returned) for calculations or quick scripts. "
        f"Isolated subprocess, {TIMEOUT_SECONDS}s timeout."
    )
    input_schema = {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python code to execute"}},
        "required": ["code"],
    }

    def run(self, code: str) -> str:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", dir=config.sandbox_dir, delete=False
            ) as f:
                f.write(code)
                script_path = f.name

            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=config.sandbox_dir,
            )
            Path(script_path).unlink(missing_ok=True)

            output = result.stdout.strip()
            error = result.stderr.strip()
            if error:
                return f"stdout:\n{output}\n\nstderr:\n{error}"
            return output if output else "(code ran with no output)"
        except subprocess.TimeoutExpired:
            return f"Execution timed out after {TIMEOUT_SECONDS}s."
        except Exception as e:
            return f"Error executing code: {e}"