"""
Sandboxed Python code execution.
Runs in a subprocess with a timeout and no network/file access beyond the sandbox,
so a bad or malicious snippet can't take down or escape the assistant process.
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
        "Execute a short Python snippet and return its stdout/stderr. "
        "Use for calculations, data processing, or quick scripts. "
        f"Runs in an isolated subprocess with a {TIMEOUT_SECONDS}s timeout — no long-running jobs."
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