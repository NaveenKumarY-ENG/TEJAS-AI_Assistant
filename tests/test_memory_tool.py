"""
Tests for RemindersTool's real-Calendar integration (tools/memory_tool.py).
integrations.google_calendar is mocked throughout — these tests are about
RemindersTool's own fallback/wiring logic, not the real Google API (see
tests/test_google_calendar.py for that).
"""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import structured
from tools.memory_tool import RemindersTool, _corrected_weekly_due_at


def test_add_reminder_without_due_at_skips_calendar_entirely():
    tool = RemindersTool()
    result = tool.run(action="add", text="Buy milk")
    assert "saved" in result.lower()
    assert "calendar" not in result.lower()


def test_add_reminder_with_due_at_falls_back_when_calendar_not_configured():
    with patch("integrations.google_calendar.available", return_value=False):
        result = RemindersTool().run(action="add", text="Wish brother happy birthday", due_at="2026-08-28T09:00:00")
    assert "saved" in result.lower()
    assert "isn't set up" in result


def test_add_reminder_creates_calendar_event_when_available():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt1", "html_link": "https://calendar.google.com/evt1"},
        ),
    ):
        result = RemindersTool().run(action="add", text="Wish brother happy birthday", due_at="2026-08-28T09:00:00")

    assert "https://calendar.google.com/evt1" in result
    saved = next(r for r in structured.list_reminders() if r["text"] == "Wish brother happy birthday")
    assert saved["calendar_event_id"] == "evt1"


def test_add_reminder_calendar_failure_still_saves_in_app():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch("integrations.google_calendar.create_reminder_event", side_effect=Exception("API error")),
    ):
        result = RemindersTool().run(action="add", text="Test calendar failure", due_at="2026-08-28T09:00:00")

    assert "saved" in result.lower()
    assert "calendar sync failed" in result
    saved = next(r for r in structured.list_reminders() if r["text"] == "Test calendar failure")
    assert saved["calendar_event_id"] is None


def test_complete_reminder_deletes_its_linked_calendar_event():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt2", "html_link": "https://calendar.google.com/evt2"},
        ),
    ):
        RemindersTool().run(action="add", text="Complete me", due_at="2026-08-28T09:00:00")

    target = next(r for r in structured.list_reminders() if r["text"] == "Complete me")

    with patch("integrations.google_calendar.delete_event") as mock_delete:
        result = RemindersTool().run(action="complete", reminder_id=target["id"])

    assert "marked done" in result
    mock_delete.assert_called_once_with("evt2")


def test_complete_reminder_without_calendar_event_never_calls_delete():
    add_result = RemindersTool().run(action="add", text="No calendar link")
    reminder_id = int(add_result.split("#")[1].split(" ")[0])

    with patch("integrations.google_calendar.delete_event") as mock_delete:
        RemindersTool().run(action="complete", reminder_id=reminder_id)

    mock_delete.assert_not_called()


def test_complete_unknown_reminder_id_reports_not_found():
    with patch("integrations.google_calendar.delete_event") as mock_delete:
        result = RemindersTool().run(action="complete", reminder_id=999999)
    assert "no reminder found" in result.lower()
    mock_delete.assert_not_called()


def test_add_reminder_with_recurrence_passes_it_through():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt3", "html_link": "https://calendar.google.com/evt3"},
        ) as mock_create,
    ):
        result = RemindersTool().run(
            action="add", text="Weekly standup", due_at="2026-08-31T09:00:00", recurrence="weekly"
        )

    mock_create.assert_called_once_with("Weekly standup", "2026-08-31T09:00:00", "weekly")
    assert "repeats weekly" in result
    saved = next(r for r in structured.list_reminders() if r["text"] == "Weekly standup")
    assert saved["recurrence"] == "weekly"


def test_corrected_weekly_due_at_lands_on_the_requested_weekday():
    # due_at here is a Monday (2026-08-31) — deliberately NOT the requested
    # weekday, to prove correction actually happens rather than passing
    # through unchanged.
    corrected = _corrected_weekly_due_at("2026-08-31T17:00:00", "friday")
    dt = datetime.fromisoformat(corrected)
    assert dt.strftime("%A").lower() == "friday"
    assert (dt.hour, dt.minute) == (17, 0)
    assert dt >= datetime.now()


def test_add_weekly_reminder_with_weekday_corrects_a_wrong_due_at():
    # Confirmed live bug: asked for "every Friday", the model computed a
    # due_at landing on a Monday instead — since the Calendar RRULE's BYDAY
    # is derived from due_at's own weekday, this silently created a
    # recurring event that fired on the wrong day every week. The weekday
    # field must override due_at's date rather than trusting it as given.
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt7", "html_link": "https://calendar.google.com/evt7"},
        ) as mock_create,
    ):
        RemindersTool().run(
            action="add",
            text="Water the plants",
            due_at="2026-08-31T17:00:00",
            recurrence="weekly",
            weekday="friday",
        )

    called_due_at = mock_create.call_args[0][1]
    dt = datetime.fromisoformat(called_due_at)
    assert dt.strftime("%A").lower() == "friday"
    assert (dt.hour, dt.minute) == (17, 0)


def test_add_weekly_reminder_without_weekday_falls_back_to_due_at_unchanged():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt8", "html_link": "https://calendar.google.com/evt8"},
        ) as mock_create,
    ):
        RemindersTool().run(
            action="add", text="No weekday given", due_at="2026-08-31T17:00:00", recurrence="weekly"
        )

    mock_create.assert_called_once_with("No weekday given", "2026-08-31T17:00:00", "weekly")


def test_add_reminder_confirmation_is_human_readable_not_raw_iso():
    result = RemindersTool().run(action="add", text="Readable date test", due_at="2026-09-05T10:00:00")
    assert "2026-09-05T10:00:00" not in result
    assert "September 05, 2026" in result
    assert "10:00 AM" in result


def test_list_reminders_shows_recurrence_marker():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt4", "html_link": "https://calendar.google.com/evt4"},
        ),
    ):
        RemindersTool().run(action="add", text="Daily journal", due_at="2026-08-27T08:00:00", recurrence="daily")

    result = RemindersTool().run(action="list")
    assert "Daily journal" in result
    assert "[repeats daily]" in result


def test_update_reminder_changes_text_and_time_and_syncs_calendar():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt5", "html_link": "https://calendar.google.com/evt5"},
        ),
    ):
        add_result = RemindersTool().run(action="add", text="QA report", due_at="2026-09-05T10:00:00")
    reminder_id = int(add_result.split("#")[1].split(" ")[0])

    with patch("integrations.google_calendar.update_event") as mock_update:
        result = RemindersTool().run(
            action="update", reminder_id=reminder_id, due_at="2026-09-12T14:00:00"
        )

    mock_update.assert_called_once_with("evt5", None, "2026-09-12T14:00:00")
    assert "updated" in result.lower()
    updated = structured.get_reminder(reminder_id)
    assert updated["due_at"] == "2026-09-12T14:00:00"
    assert updated["text"] == "QA report"  # unchanged since text wasn't given


def test_update_reminder_without_calendar_event_skips_calendar_call():
    add_result = RemindersTool().run(action="add", text="Local only reminder")
    reminder_id = int(add_result.split("#")[1].split(" ")[0])

    with patch("integrations.google_calendar.update_event") as mock_update:
        result = RemindersTool().run(action="update", reminder_id=reminder_id, text="Renamed")

    mock_update.assert_not_called()
    assert "updated" in result.lower()
    assert structured.get_reminder(reminder_id)["text"] == "Renamed"


def test_update_reminder_requires_at_least_one_field():
    add_result = RemindersTool().run(action="add", text="Needs a change")
    reminder_id = int(add_result.split("#")[1].split(" ")[0])
    result = RemindersTool().run(action="update", reminder_id=reminder_id)
    assert "at least" in result.lower()


def test_update_unknown_reminder_id_reports_not_found():
    result = RemindersTool().run(action="update", reminder_id=999999, text="X")
    assert "no reminder found" in result.lower()


def test_delete_reminder_removes_it_and_its_calendar_event():
    with (
        patch("integrations.google_calendar.available", return_value=True),
        patch(
            "integrations.google_calendar.create_reminder_event",
            return_value={"event_id": "evt6", "html_link": "https://calendar.google.com/evt6"},
        ),
    ):
        add_result = RemindersTool().run(action="add", text="Delete me", due_at="2026-09-05T10:00:00")
    reminder_id = int(add_result.split("#")[1].split(" ")[0])

    with patch("integrations.google_calendar.delete_event") as mock_delete:
        result = RemindersTool().run(action="delete", reminder_id=reminder_id)

    mock_delete.assert_called_once_with("evt6")
    assert "deleted" in result.lower()
    assert structured.get_reminder(reminder_id) is None


def test_delete_unknown_reminder_id_reports_not_found():
    result = RemindersTool().run(action="delete", reminder_id=999999)
    assert "no reminder found" in result.lower()


def test_reminder_listing_includes_id_due_date_and_recurrence():
    RemindersTool().run(action="add", text="Weekly review", due_at="2026-08-31T09:00:00", recurrence="weekly")
    listing = structured.reminder_listing()
    assert "Weekly review" in listing
    # Human-readable, with a real (code-computed) weekday — not raw ISO —
    # confirmed live as a real bug otherwise: handed only raw ISO dates, the
    # model tried to compute weekday names itself and got them wrong.
    assert "August 31, 2026" in listing
    assert "2026-08-31T09:00:00" not in listing
    assert "[repeats weekly]" in listing


def test_reminder_listing_empty_when_no_active_reminders():
    for r in structured.list_reminders():
        structured.delete_reminder(r["id"])
    assert structured.reminder_listing() == ""
