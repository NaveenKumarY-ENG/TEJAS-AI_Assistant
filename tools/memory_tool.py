"""
Exposes the structured memory store (reminders, facts) as tools the LLM can call.
This is what lets the assistant say "remind me to X" and actually persist it.
"""
from memory import structured
from tools.base import Tool


class AddReminderTool(Tool):
    name = "add_reminder"
    description = "Save a reminder or task for the user to be recalled later."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to be reminded of"},
            "due_at": {"type": "string", "description": "Optional due date/time, ISO format or natural language"},
        },
        "required": ["text"],
    }

    def run(self, text: str, due_at: str | None = None) -> str:
        reminder_id = structured.add_reminder(text, due_at)
        return f"Reminder #{reminder_id} saved: '{text}'" + (f" (due {due_at})" if due_at else "")


class ListRemindersTool(Tool):
    name = "list_reminders"
    description = "List all active (not yet completed) reminders."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        reminders = structured.list_reminders()
        if not reminders:
            return "No active reminders."
        return "\n".join(f"#{r['id']}: {r['text']}" + (f" (due {r['due_at']})" if r["due_at"] else "") for r in reminders)


class CompleteReminderTool(Tool):
    name = "complete_reminder"
    description = "Mark a reminder as done, given its ID."
    input_schema = {
        "type": "object",
        "properties": {"reminder_id": {"type": "integer", "description": "The reminder's ID"}},
        "required": ["reminder_id"],
    }

    def run(self, reminder_id: int) -> str:
        ok = structured.complete_reminder(reminder_id)
        return f"Reminder #{reminder_id} marked done." if ok else f"No reminder found with ID {reminder_id}."


class RememberFactTool(Tool):
    name = "remember_fact"
    description = "Store a fact or preference about the user for future reference (e.g. 'favorite_language: Python')."
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