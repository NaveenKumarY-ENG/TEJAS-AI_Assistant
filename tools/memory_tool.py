"""
Exposes the structured memory store (reminders, facts) as tools the LLM can call.
This is what lets the assistant say "remind me to X" and actually persist it.
"""
from memory import structured
from tools.base import Tool


class RemindersTool(Tool):
    # Add/list/complete are one tool (not three) to keep the per-turn
    # tool-schema payload smaller — on CPU-only local inference, every tool
    # in the schema adds real, measured latency to every request (see
    # agent/llm_client.py), so tool *count* matters, not just description length.
    name = "manage_reminders"
    description = "Add, list, or complete reminders. Set action to 'add', 'list', or 'complete'."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "complete"], "description": "Which action to perform"},
            "text": {"type": "string", "description": "Reminder text — required when action is 'add'"},
            "due_at": {"type": "string", "description": "Optional due date/time for 'add', ISO or natural language"},
            "reminder_id": {"type": "integer", "description": "Reminder ID — required when action is 'complete'"},
        },
        "required": ["action"],
    }

    def run(self, action: str, text: str | None = None, due_at: str | None = None, reminder_id: int | None = None) -> str:
        if action == "add":
            if not text:
                return "Error: 'text' is required for the 'add' action."
            new_id = structured.add_reminder(text, due_at)
            return f"Reminder #{new_id} saved: '{text}'" + (f" (due {due_at})" if due_at else "")
        if action == "list":
            reminders = structured.list_reminders()
            if not reminders:
                return "No active reminders."
            return "\n".join(
                f"#{r['id']}: {r['text']}" + (f" (due {r['due_at']})" if r["due_at"] else "") for r in reminders
            )
        if action == "complete":
            if reminder_id is None:
                return "Error: 'reminder_id' is required for the 'complete' action."
            ok = structured.complete_reminder(reminder_id)
            return f"Reminder #{reminder_id} marked done." if ok else f"No reminder found with ID {reminder_id}."
        return f"Unknown action '{action}'. Use 'add', 'list', or 'complete'."


class RememberFactTool(Tool):
    name = "remember_fact"
    description = "Store a fact/preference about the user for future recall (e.g. 'favorite_language: Python')."
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short label for the fact"},
            "value": {"type": "string", "description": "The fact itself"},
        },
        "required": ["key", "value"],
    }

    def run(self, key: str, value: str) -> str:
        structured.save_fact(key, value)
        return f"Remembered: {key} = {value}"
