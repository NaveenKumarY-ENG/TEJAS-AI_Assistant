"""
System information tool - read-only host stats.
Deliberately read-only: no process killing, no shutdown, no config changes.
"""
import platform
import shutil

from tools.base import Tool


class SystemInfoTool(Tool):
    name = "get_system_info"
    description = "OS/CPU/disk info about the host machine."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        try:
            total, used, free = shutil.disk_usage("/")
            gb = 1024 ** 3
            lines = [
                f"OS: {platform.system()} {platform.release()}",
                f"Machine: {platform.machine()}",
                f"Processor: {platform.processor() or 'unknown'}",
                f"Python: {platform.python_version()}",
                f"Disk: {used // gb} GB used / {total // gb} GB total ({free // gb} GB free)",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting system info: {e}"