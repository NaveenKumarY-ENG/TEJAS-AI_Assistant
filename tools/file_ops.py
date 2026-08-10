"""
File operations tool — confined to a sandbox directory so the assistant
can never read/write/delete arbitrary files on the host machine.
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


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a text file inside the assistant's sandbox directory."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative path to the file"}},
        "required": ["path"],
    }

    def run(self, path: str) -> str:
        try:
            target = _safe_path(path)
            if not target.exists():
                return f"File not found: {path}"
            return target.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write text content to a file inside the assistant's sandbox directory. Overwrites if it exists."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def run(self, path: str, content: str) -> str:
        try:
            target = _safe_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {path}"
        except Exception as e:
            return f"Error writing file: {e}"


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files and folders inside the assistant's sandbox directory."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative directory path, default is root"}},
        "required": [],
    }

    def run(self, path: str = ".") -> str:
        try:
            target = _safe_path(path)
            if not target.exists():
                return f"Directory not found: {path}"
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            return "\n".join(entries) if entries else "(empty directory)"
        except Exception as e:
            return f"Error listing files: {e}"