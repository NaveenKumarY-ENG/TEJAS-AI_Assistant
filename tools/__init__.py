"""
Tool registry. To add a new tool: write the class in its own file (subclassing
Tool), then add one line here. Nothing else in the system needs to change.
"""
from tools.datetime_tool import DateTimeTool
from tools.system_info import SystemInfoTool
from tools.weather import WeatherTool
from tools.base import Tool
from tools.code_exec import CodeExecutionTool
from tools.file_ops import ListFilesTool, ReadFileTool, WriteFileTool
from tools.memory_tool import (
    AddReminderTool,
    CompleteReminderTool,
    ListRemindersTool,
    RememberFactTool,
)
from tools.web_search import WebSearchTool

ALL_TOOLS: list[Tool] = [
    WebSearchTool(),
    WeatherTool(),
    DateTimeTool(),
    SystemInfoTool(),
    ReadFileTool(),
    WriteFileTool(),
    ListFilesTool(),
    CodeExecutionTool(),
    AddReminderTool(),
    ListRemindersTool(),
    CompleteReminderTool(),
    RememberFactTool(),
]

TOOL_MAP: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def get_tool_schemas() -> list[dict]:
    """Ollama/OpenAI-style function-calling schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in ALL_TOOLS
    ]


def execute_tool(name: str, tool_input: dict) -> str:
    tool = TOOL_MAP.get(name)
    if tool is None:
        return f"Unknown tool: {name}"
    try:
        return tool.run(**tool_input)
    except Exception as e:
        return f"Tool '{name}' raised an unexpected error: {e}"