"""
Web search tool. Uses Tavily's API (simple, LLM-oriented search API) by default.
Swap the implementation here if you prefer Serper, Bing, or another provider —
the rest of the system only depends on the Tool interface.
"""
import requests

from config import config
from tools.base import Tool

# Words that show up in placeholder API keys (".env.example"'s own text, or
# a user's own "fill this in" edit) but never in a real Tavily key, which is
# just a random-looking token after the "tvly-" prefix. Pattern-based rather
# than an exact match on today's .env.example wording specifically, so this
# still catches an unfilled key even if that placeholder text changes later.
_PLACEHOLDER_MARKERS = ("your", "here", "xxx", "example", "replace", "changeme", "<", ">")


def _looks_unconfigured(key: str) -> bool:
    if not key:
        return True
    lowered = key.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for current info (news, facts, prices) not in your training data."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The search query"}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        # .env.example ships with a literal placeholder value — copying it to
        # .env without editing it is truthy (`not config.search_api_key`
        # alone doesn't catch it), so without this check the placeholder
        # sails past the "not configured" branch straight into a real, always
        # -failing Tavily call. That returned a raw HTTP error ("401
        # Unauthorized") confusing enough that a local model once used it as
        # cover to answer from (wrong) memory instead of just relaying it —
        # a clear, unambiguous "not configured" message is what the system
        # prompt's anti-hallucination rule is actually built to handle.
        if _looks_unconfigured(config.search_api_key):
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