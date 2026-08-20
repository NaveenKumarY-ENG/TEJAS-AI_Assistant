"""
Web search tool. Uses Tavily's API (simple, LLM-oriented search API) by default.
Swap the implementation here if you prefer Serper, Bing, or another provider —
the rest of the system only depends on the Tool interface.
"""
import requests

from config import config
from tools.base import Tool


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for current info (news, facts, prices) not in your training data."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The search query"}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        if not config.search_api_key:
            return (
                "Web search is not configured. Set SEARCH_API_KEY in your .env "
                "(get a free key at tavily.com) to enable this tool."
            )
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": config.search_api_key,
                    "query": query,
                    "max_results": 5,
                    "include_answer": True,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            parts = []
            if data.get("answer"):
                parts.append(f"Quick answer: {data['answer']}")
            for r in data.get("results", [])[:5]:
                parts.append(f"- {r['title']}: {r['content'][:200]}... ({r['url']})")
            return "\n".join(parts) if parts else "No results found."
        except Exception as e:
            return f"Error during web search: {e}"