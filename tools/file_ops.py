"""
File operations tool — confined to a sandbox directory so the assistant
can never read/write/delete arbitrary files on the host machine.

Read/write/list are one tool (not three) to keep the per-turn tool-schema
payload smaller — on CPU-only local inference, every tool in the schema adds
real, measured latency to every single request (see llm_client.py), so tool
*count* matters, not just description length.
"""
from pathlib import Path

from config import config
from tools.base import Tool


def _safe_path(relative_path: str) -> Path:
    """Resolve a user-given path and guarantee it stays inside the sandbox."""
    sandbox = Path(config.sandbox_dir).resolve()
    target = (sandbox / relative_path).resolve()
    if sandbox not in target.parents and target != sandbox:
        raise ValueError(f"Path '{relative_path}' escapes the sandbox — refused.")
    return target


class FileOpsTool(Tool):
    name = "file_ops"
    description = "Read, write, or list files in the sandbox directory. Set operation to 'read', 'write', or 'list'."
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["read", "write", "list"], "description": "Which action to perform"},
            "path": {"type": "string", "description": "Relative file/dir path. Optional for 'list' (defaults to root)."},
            "content": {"type": "string", "description": "Content to write — required when operation is 'write'"},
        },
        "required": ["operation"],
    }

    def run(self, operation: str, path: str = ".", content: str | None = None) -> str:
        if operation == "read":
            return self._read(path)
        if operation == "write":
            if content is None:
                return "Error: 'content' is required for the 'write' operation."
            return self._write(path, content)
        if operation == "list":
            return self._list(path)
        return f"Unknown operation '{operation}'. Use 'read', 'write', or 'list'."

    def _read(self, path: str) -> str:
        try:
            target = _safe_path(path)
            if not target.exists():
                return f"File not found: {path}"
            return target.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

    def _write(self, path: str, content: str) -> str:
        try:
            target = _safe_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    def _list(self, path: str) -> str:
        try:
            target = _safe_path(path)
            if not target.exists():
                return f"Directory not found: {path}"
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            return "\n".join(entries) if entries else "(empty directory)"
        except Exception as e:
            return f"Error listing files: {e}"
