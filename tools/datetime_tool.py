"""
Date and time tool. The current date/time in system local time is already
injected into the outgoing prompt every turn (see config.current_time_context,
agent/loop.py's _messages_for_llm), so the model never needs to call this
for the common case. This tool only covers the remaining case: a specific
OTHER timezone.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.base import Tool


class DateTimeTool(Tool):
    name = "get_current_datetime"
    description = (
        "Get the date/time in a specific OTHER timezone (local time is already known). "
        "Only for explicit other-timezone asks, e.g. 'what time is it in Tokyo'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'Asia/Kolkata'. Omit this argument entirely for the user's own local time.",
            }
        },
        "required": [],
    }

    def run(self, timezone: str | None = None) -> str:
        tz = None
        if timezone:
            try:
                tz = ZoneInfo(timezone)
            except Exception:
                # Small local models sometimes echo descriptive schema text
                # (e.g. a literal "system local time") or an invalid city
                # name as the argument instead of omitting it. Falling back
                # to local time here — rather than returning an error the
                # model then has to explain — avoids a confirmed failure
                # mode where the model treats that error as license to
                # fabricate a plausible-sounding date instead.
                tz = None
        now = datetime.now(tz)
        tz_label = timezone if tz else "system local time"
        return f"Current date and time ({tz_label}): {now.strftime('%A, %d %B %Y, %I:%M %p')}"