"""
Date and time tool. The current date/time in system local time is already
injected into the system prompt every turn (see config.formatted_system_prompt),
so the model never needs to call this for the common case. This tool only
covers the remaining case: a specific OTHER timezone.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.base import Tool


class DateTimeTool(Tool):
    name = "get_current_datetime"
    description = (
        "Get the current date and time in a SPECIFIC timezone other than the user's "
        "system local time (which is already given to you in the system prompt). Only "
        "call this when the user asks about a different timezone, e.g. 'what time is "
        "it in Tokyo'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'Asia/Kolkata'. Defaults to system local time.",
            }
        },
        "required": [],
    }

    def run(self, timezone: str | None = None) -> str:
        try:
            now = datetime.now(ZoneInfo(timezone)) if timezone else datetime.now()
            tz_label = timezone or "system local time"
            return (
                f"Current date and time ({tz_label}): "
                f"{now.strftime('%A, %d %B %Y, %I:%M %p')}"
            )
        except Exception as e:
            return f"Error getting date/time: {e}"