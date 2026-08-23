"""
Tool registry. To add a new tool: write the class in its own file (subclassing
Tool), then add one line here. Nothing else in the system needs to change.
"""
from tools.datetime_tool import DateTimeTool
from tools.system_info import SystemInfoTool
from tools.weather import WeatherTool
from tools.base import Tool
from tools.code_exec import CodeExecutionTool
from tools.file_ops import FileOpsTool
from tools.memory_tool import RememberFactTool, RemindersTool
from tools.web_search import WebSearchTool
from tools.knowledge_tool import SearchKnowledgeTool

# Kept to 9 tools (down from 12, up from 8 with the addition of
# search_knowledge) — read/write/list files and add/list/complete reminders
# are each one tool with an operation/action parameter instead of three. On
# CPU-only local inference every tool in this list adds real, measured
# prompt-processing latency to every request (see agent/llm_client.py), so
# tool count directly affects response time — search_knowledge earns its
# spot because the knowledge base is useless to the model without it.
ALL_TOOLS: list[Tool] = [
    WebSearchTool(),
    WeatherTool(),
    DateTimeTool(),
    SystemInfoTool(),
    FileOpsTool(),
    CodeExecutionTool(),
    RemindersTool(),
    RememberFactTool(),
    SearchKnowledgeTool(),
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