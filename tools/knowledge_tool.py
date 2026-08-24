"""
Exposes the knowledge base (memory/knowledge.py) as a tool the LLM can call
to search documents/notes the user has uploaded — separate from
remember_fact/manage_reminders (tools/memory_tool.py), which are about
things the assistant itself recorded, not user-supplied source material.

The knowledge base is already searched proactively every turn (see
agent/loop.py's _messages_for_llm) — relying on the model to decide to call
this tool was confirmed live, repeatedly, to be unreliable on a 7B local
model for exactly the questions that matter most (a bare filename
reference). This tool stays registered anyway, as a supplementary way for
the model to re-search with a more targeted query mid-conversation (e.g. a
follow-up that wants something more specific than what the user's own raw
message would proactively surface).
"""
from memory import knowledge
from tools.base import Tool


class SearchKnowledgeTool(Tool):
    name = "search_knowledge"
    description = "Search documents/notes/images/PDFs the user has uploaded to their knowledge base, with a query different from (or more specific than) the user's own message."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for"}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        return knowledge.format_search_results(knowledge.search(query))
