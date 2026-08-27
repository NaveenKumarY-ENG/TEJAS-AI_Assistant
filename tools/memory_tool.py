"""
Exposes the structured memory store (reminders, facts) as tools the LLM can call.
This is what lets the assistant say "remind me to X" and actually persist it.

A reminder with a due_at additionally becomes a real Google Calendar event
(integrations/google_calendar.py) with a popup + email notification — see
that module's docstring for why this beats a self-hosted scheduler (Google's
own infrastructure fires it, reliably, even if this app's server isn't
running at the time). Falls back to an in-app-only reminder, same as before
this existed, whenever Calendar isn't configured or the API call fails —
never blocks saving the reminder itself.
"""
import logging
from datetime import datetime, timedelta

from memory import structured
from memory.structured import format_due
from tools.base import Tool

logger = logging.getLogger("assistant.memory_tool")

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _corrected_weekly_due_at(due_at: str, weekday: str) -> str:
    """Weekly recurrence's Calendar RRULE derives its BYDAY from due_at's own
    weekday (see integrations/google_calendar.py's _build_rrule), so due_at
    must actually fall on the weekday the user asked for. Confirmed live as a
    real bug: asked for "every Friday", the model computed a due_at landing
    on a Monday — silently creating a recurring event that fires on the wrong
    day every week. An explicit weekday field lets code compute the correct
    next occurrence deterministically, trusting the model only for the day
    name and time-of-day, never for "what date is next Friday" arithmetic."""
    target = _WEEKDAYS.index(weekday.lower())
    time_part = datetime.fromisoformat(due_at)
    now = datetime.now()
    candidate = now.replace(hour=time_part.hour, minute=time_part.minute, second=0, microsecond=0)
    candidate += timedelta(days=(target - now.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate.isoformat()


class RemindersTool(Tool):
    # One tool with an action param (not five) to keep the per-turn
    # tool-schema payload smaller — on CPU-only local inference, every tool
    # in the schema adds real, measured latency to every request (see
    # agent/llm_client.py), so tool *count* matters, not just description length.
    name = "manage_reminders"
    description = "Add, list, complete, update, or delete reminders. Set action accordingly."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "complete", "update", "delete"],
                "description": "Which action to perform",
            },
            "text": {
                "type": "string",
                "description": "Reminder text — required for 'add'; new text for 'update' (optional, only if changing it)",
            },
            "due_at": {
                "type": "string",
                "description": (
                    "Due date/time, as an exact ISO 8601 datetime (e.g. '2026-08-28T09:00:00') "
                    "computed from the current date/time already given to you this turn — never a "
                    "relative phrase like 'tomorrow'. For 'add', creates a real Google Calendar event "
                    "with a popup + email reminder at that time, if Calendar is set up. For 'update', "
                    "the new time to reschedule to (optional, only if changing it)."
                ),
            },
            "recurrence": {
                "type": "string",
                "enum": ["none", "daily", "weekly", "monthly"],
                "description": (
                    "Only for 'add', with a due_at also given. Never write recurrence rule syntax "
                    "yourself, just pick one of these values. If 'weekly', also give 'weekday'. "
                    "Defaults to 'none'."
                ),
            },
            "weekday": {
                "type": "string",
                "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                "description": (
                    "Required when recurrence is 'weekly' — the day of the week this should repeat "
                    "on (e.g. the user said 'every Friday' -> 'friday'). Give this explicitly instead "
                    "of relying on due_at's date alone: computing 'what date is next Friday' correctly "
                    "is error-prone, and this field lets code compute the right date for you."
                ),
            },
            "reminder_id": {
                "type": "integer",
                "description": "Reminder ID — required for 'complete', 'update', and 'delete' (use 'list' first if you don't already know it)",
            },
        },
        "required": ["action"],
    }

    def run(
        self,
        action: str,
        text: str | None = None,
        due_at: str | None = None,
        recurrence: str = "none",
        reminder_id: int | None = None,
        weekday: str | None = None,
    ) -> str:
        if action == "add":
            if not text:
                return "Error: 'text' is required for the 'add' action."
            return self._add(text, due_at, recurrence, weekday)
        if action == "list":
            reminders = structured.list_reminders()
            if not reminders:
                return "No active reminders."
            lines = []
            for r in reminders:
                line = f"#{r['id']}: {r['text']}"
                if r["due_at"]:
                    line += f" (due {format_due(r['due_at'])})"
                if r.get("recurrence", "none") != "none":
                    line += f" [repeats {r['recurrence']}]"
                lines.append(line)
            return "\n".join(lines)
        if action == "complete":
            if reminder_id is None:
                return "Error: 'reminder_id' is required for the 'complete' action."
            return self._complete(reminder_id)
        if action == "update":
            if reminder_id is None:
                return "Error: 'reminder_id' is required for the 'update' action."
            return self._update(reminder_id, text, due_at)
        if action == "delete":
            if reminder_id is None:
                return "Error: 'reminder_id' is required for the 'delete' action."
            return self._delete(reminder_id)
        return f"Unknown action '{action}'. Use 'add', 'list', 'complete', 'update', or 'delete'."

    def _add(
        self, text: str, due_at: str | None, recurrence: str = "none", weekday: str | None = None
    ) -> str:
        if recurrence == "weekly" and due_at and weekday:
            due_at = _corrected_weekly_due_at(due_at, weekday)

        calendar_event_id = None
        calendar_note = ""
        if due_at:
            from integrations import google_calendar

            if google_calendar.available():
                try:
                    event = google_calendar.create_reminder_event(text, due_at, recurrence)
                    calendar_event_id = event["event_id"]
                    calendar_note = f" — added to your calendar: {event['html_link']}"
                except Exception:
                    logger.exception("Failed to create Calendar event for reminder")
                    calendar_note = " (calendar sync failed this time — saved as an in-app reminder only)"
            else:
                calendar_note = " (calendar sync isn't set up — saved as an in-app reminder only, see README)"

        new_id = structured.add_reminder(text, due_at, calendar_event_id, recurrence)
        due_note = f" (due {format_due(due_at)})" if due_at else ""
        recurrence_note = f" [repeats {recurrence}]" if recurrence != "none" else ""
        return f"Reminder #{new_id} saved: '{text}'{due_note}{recurrence_note}{calendar_note}"

    def _complete(self, reminder_id: int) -> str:
        reminder = structured.get_reminder(reminder_id)
        ok = structured.complete_reminder(reminder_id)
        if not ok:
            return f"No reminder found with ID {reminder_id}."
        if reminder and reminder.get("calendar_event_id"):
            from integrations import google_calendar

            google_calendar.delete_event(reminder["calendar_event_id"])
        return f"Reminder #{reminder_id} marked done."

    def _update(self, reminder_id: int, text: str | None, due_at: str | None) -> str:
        reminder = structured.get_reminder(reminder_id)
        if reminder is None:
            return f"No reminder found with ID {reminder_id}."
        if text is None and due_at is None:
            return "Error: give at least a new 'text' or 'due_at' to update."

        calendar_note = ""
        if reminder.get("calendar_event_id"):
            from integrations import google_calendar

            try:
                google_calendar.update_event(reminder["calendar_event_id"], text, due_at)
                calendar_note = " (calendar event updated too)"
            except Exception:
                logger.exception("Failed to update Calendar event for reminder")
                calendar_note = " (calendar sync failed this time — updated the in-app reminder only)"

        structured.update_reminder(reminder_id, text, due_at)
        updated = structured.get_reminder(reminder_id)
        due_note = f" (due {format_due(updated['due_at'])})" if updated and updated["due_at"] else ""
        return f"Reminder #{reminder_id} updated: '{updated['text']}'{due_note}{calendar_note}"

    def _delete(self, reminder_id: int) -> str:
        reminder = structured.get_reminder(reminder_id)
        if reminder and reminder.get("calendar_event_id"):
            from integrations import google_calendar

            google_calendar.delete_event(reminder["calendar_event_id"])
        ok = structured.delete_reminder(reminder_id)
        return f"Reminder #{reminder_id} deleted." if ok else f"No reminder found with ID {reminder_id}."


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
