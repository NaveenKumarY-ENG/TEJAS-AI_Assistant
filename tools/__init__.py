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
from tools.shopping_tool import ShopAmazonTool
from tools.order_tool import OrderAmazonTool

# Kept deliberately low (11, up from 8 originally) — read/write/list files
# and add/list/complete/update/delete reminders are each one tool with an
# operation/action parameter instead of many. On CPU-only local inference
# every tool in this list adds real, measured prompt-processing latency to
# every request (see agent/llm_client.py), so tool count directly affects
# response time — search_knowledge, shop_amazon, and order_amazon each earn
# their spot on capability alone (none of those exist without their own tool).
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
    ShopAmazonTool(),
    OrderAmazonTool(),
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