"""Real Google Calendar reminders — creating an event here is what makes a
reminder fire in the real world (a popup + email notification) even if this
app's server isn't running at the time it's due, since Google's own
infrastructure delivers both, not a scheduler of ours.

Same lazy-availability, fail-soft shape as agent/tts.py / memory/ocr.py:
missing setup (no credentials, no token yet) makes available() return False
rather than raising, so tools/memory_tool.py can degrade to an in-app-only
reminder instead of crashing the turn.

Setup is two steps, both one-time and documented in README.md:
1. Download an OAuth "Desktop app" client secret from Google Cloud Console
   and save it as data/google_credentials.json.
2. Run `python -m integrations.google_calendar` once — opens a browser for
   a single consent click, then writes data/google_token.json (a refresh
   token; auto-renews silently after this, no further prompts).

Deliberately never triggers that interactive browser consent flow from
inside available()/create_reminder_event() — those run inside a live server
request, which would just hang forever waiting for a click nobody sees.
Only the explicit `python -m` entry point at the bottom of this file does.
"""
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("assistant.google_calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CREDENTIALS_PATH = DATA_DIR / "google_credentials.json"
TOKEN_PATH = DATA_DIR / "google_token.json"

_availability_cache: bool | None = None
_availability_lock = threading.Lock()


def _get_credentials():
    """Loads a refresh-token-backed Credentials object, refreshing the
    access token if it's expired. Returns None if no usable token exists
    yet — never launches the interactive consent flow (see module docstring)."""
    if not TOKEN_PATH.exists():
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
        return creds
    return None


_HTTP_TIMEOUT_SECONDS = 10


def _build_service(creds):
    """Builds the Calendar API service with a bounded request timeout.
    Confirmed live as a real, serious bug otherwise: googleapiclient's
    underlying httplib2 transport has NO default timeout at all — a slow or
    dropped connection made a real create_reminder_event() call hang
    indefinitely, freezing that entire chat turn (and the thread running
    it) forever with no way to recover short of restarting the server.
    credentials= and http= are mutually exclusive on build(), so the
    credentials have to be wrapped into the timeout-bound http object
    instead of passed directly."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS))
    return build("calendar", "v3", http=http)


def _compute_availability() -> bool:
    if not CREDENTIALS_PATH.exists():
        return False
    try:
        return _get_credentials() is not None
    except Exception:
        logger.exception("Google Calendar unavailable (credential/refresh error)")
        return False


def available() -> bool:
    """Whether real Calendar reminders can be created right now. Cached
    after the first call within this process."""
    global _availability_cache
    if _availability_cache is None:
        with _availability_lock:
            if _availability_cache is None:
                _availability_cache = _compute_availability()
    return _availability_cache


_local_zone_name: str | None = None


def _local_zone() -> str:
    """The machine's IANA zone name (e.g. "Asia/Calcutta"), resolved once.
    Needed instead of just a fixed UTC offset (an earlier version of this
    module embedded one directly into the dateTime string) because Google
    Calendar rejects a recurring event without an explicit named timeZone —
    confirmed live: creating a weekly reminder with only an offset-bearing
    dateTime failed with "400: Missing time zone definition for start time."
    A fixed offset can't tell Google how to correctly handle a recurring
    event's future occurrences across a DST transition anyway, so this is
    used for every event now, not just recurring ones."""
    global _local_zone_name
    if _local_zone_name is None:
        import tzlocal

        _local_zone_name = tzlocal.get_localzone_name()
    return _local_zone_name


def _event_time(iso_datetime: str) -> dict:
    """Builds the {"dateTime", "timeZone"} dict Calendar's API expects for
    an event's start/end — a plain local wall-clock time (no UTC offset
    needed once timeZone is given) plus the machine's real IANA zone."""
    dt = datetime.fromisoformat(iso_datetime)
    return {"dateTime": dt.replace(tzinfo=None).isoformat(), "timeZone": _local_zone()}


_RRULE_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _build_rrule(recurrence: str, due_at: str) -> str:
    """Builds the exact RRULE string deterministically from a simple enum —
    the LLM only ever picks one of 4 values (see tools/memory_tool.py's
    schema), never hand-writes RRULE syntax itself. Weekly derives BYDAY
    from due_at's own weekday, so "every Monday" only requires the model to
    pick a Monday due_at and recurrence="weekly" — it never has to separately
    name which day in RRULE's own vocabulary."""
    if recurrence == "daily":
        return "RRULE:FREQ=DAILY"
    if recurrence == "weekly":
        weekday = _RRULE_WEEKDAYS[datetime.fromisoformat(due_at).weekday()]
        return f"RRULE:FREQ=WEEKLY;BYDAY={weekday}"
    if recurrence == "monthly":
        return "RRULE:FREQ=MONTHLY"
    raise ValueError(f"Unknown recurrence: {recurrence!r}")


def create_reminder_event(text: str, due_at: str, recurrence: str = "none") -> dict:
    """Creates a real Calendar event with both a popup and an email
    reminder firing at the exact due time. recurrence ("none"/"daily"/
    "weekly"/"monthly") makes it a real repeating event — deleting the
    returned event_id later (see delete_event) cancels the whole series,
    which is the correct simple behavior for a personal reminder with no
    need to track individual occurrences. Returns {"event_id", "html_link"}.
    Raises on failure — the caller (tools/memory_tool.py) decides fallback
    messaging; this never swallows an error itself since the caller needs
    to know whether it actually succeeded."""
    creds = _get_credentials()
    if creds is None:
        raise RuntimeError("Google Calendar isn't authorized yet — see README.md's setup steps.")

    service = _build_service(creds)
    body = {
        "summary": text,
        "start": _event_time(due_at),
        "end": _event_time((datetime.fromisoformat(due_at) + timedelta(minutes=30)).isoformat()),
        # minutes=0 fires exactly at the event's own start time — this event
        # exists purely to carry the reminder, not as a real calendar block.
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 0}, {"method": "email", "minutes": 0}],
        },
    }
    if recurrence != "none":
        body["recurrence"] = [_build_rrule(recurrence, due_at)]
    created = service.events().insert(calendarId="primary", body=body).execute()
    return {"event_id": created["id"], "html_link": created.get("htmlLink", "")}


def update_event(event_id: str, text: str | None = None, due_at: str | None = None) -> dict:
    """Partial update (PATCH, not a full replace) of an existing reminder's
    Calendar event — only the fields actually being changed are sent, so an
    update that only changes the time doesn't need to resend the summary
    too. Returns {"event_id", "html_link"}. Raises on failure, same posture
    as create_reminder_event."""
    creds = _get_credentials()
    if creds is None:
        raise RuntimeError("Google Calendar isn't authorized yet — see README.md's setup steps.")

    body: dict = {}
    if text is not None:
        body["summary"] = text
    if due_at is not None:
        body["start"] = _event_time(due_at)
        body["end"] = _event_time((datetime.fromisoformat(due_at) + timedelta(minutes=30)).isoformat())

    service = _build_service(creds)
    updated = service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
    return {"event_id": updated["id"], "html_link": updated.get("htmlLink", "")}


def delete_event(event_id: str) -> None:
    """Best-effort delete — swallows any failure. Called when a reminder is
    marked complete; the reminder is being marked done either way, so a
    Calendar-side hiccup (already deleted, transient API error) shouldn't
    block that."""
    try:
        creds = _get_credentials()
        if creds is None:
            return
        service = _build_service(creds)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
    except Exception:
        logger.warning("Failed to delete Calendar event %s (non-fatal)", event_id, exc_info=True)


def _authorize() -> None:
    """One-time interactive setup — run as `python -m integrations.google_calendar`.
    Opens a browser for a single consent click, then writes the refresh
    token to TOKEN_PATH. Never called from server request handling."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS_PATH.exists():
        print(f"Missing {CREDENTIALS_PATH} — download an OAuth Desktop app client secret "
              "from Google Cloud Console first. See README.md's setup steps.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    print(f"Authorized — wrote {TOKEN_PATH}. Google Calendar reminders are now enabled.")


if __name__ == "__main__":
    _authorize()
