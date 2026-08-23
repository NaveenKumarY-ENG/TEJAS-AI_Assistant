"""
Exposes the knowledge base (memory/knowledge.py) as a tool the LLM can call
to search documents/notes the user has uploaded — separate from
remember_fact/manage_reminders (tools/memory_tool.py), which are about
things the assistant itself recorded, not user-supplied source material.
"""
from memory import knowledge
from tools.base import Tool


class SearchKnowledgeTool(Tool):
    name = "search_knowledge"
    description = "Search documents/notes the user has uploaded to their knowledge base for relevant information."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for"}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        results = knowledge.search(query)
        if not results:
            return "No relevant documents found in the knowledge base."
        return "\n\n".join(f"From '{r['filename']}': {r['text']}" for r in results)
