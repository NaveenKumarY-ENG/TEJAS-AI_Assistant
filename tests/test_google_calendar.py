"""
Tests for integrations/google_calendar.py. No real Google API calls —
google.oauth2.credentials.Credentials and googleapiclient.discovery.build
are mocked throughout; these tests are about this module's own
availability/fallback logic and the exact event payload it builds, not
Google's actual API behavior.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations import google_calendar


def test_build_service_uses_a_bounded_http_timeout():
    """Regression test for a real bug found live: googleapiclient's
    underlying httplib2 transport has no default timeout at all — a slow
    or dropped connection made a real create_reminder_event() call hang
    indefinitely, freezing the whole chat turn (and the thread running it)
    with no way to recover short of restarting the server."""
    import httplib2

    fake_creds = MagicMock(valid=True)
    with patch("googleapiclient.discovery.build") as mock_build:
        google_calendar._build_service(fake_creds)

    _, kwargs = mock_build.call_args
    authorized_http = kwargs["http"]
    assert isinstance(authorized_http.http, httplib2.Http)
    assert authorized_http.http.timeout == google_calendar._HTTP_TIMEOUT_SECONDS


def test_available_false_when_no_credentials_file(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "CREDENTIALS_PATH", tmp_path / "missing_credentials.json")
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "missing_token.json")
    monkeypatch.setattr(google_calendar, "_availability_cache", None)
    assert google_calendar.available() is False


def test_available_true_when_valid_token_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.CREDENTIALS_PATH.write_text("{}")
    google_calendar.TOKEN_PATH.write_text("{}")
    monkeypatch.setattr(google_calendar, "_availability_cache", None)

    fake_creds = MagicMock(valid=True)
    with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds):
        assert google_calendar.available() is True


def test_get_credentials_returns_none_when_no_token(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "missing_token.json")
    assert google_calendar._get_credentials() is None


def test_get_credentials_refreshes_expired_token(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")

    fake_creds = MagicMock(valid=False, expired=True, refresh_token="r1")
    fake_creds.to_json.return_value = "{}"
    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("google.auth.transport.requests.Request"),
    ):
        result = google_calendar._get_credentials()

    assert result is fake_creds
    fake_creds.refresh.assert_called_once()


def test_create_reminder_event_builds_popup_and_email_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")

    fake_creds = MagicMock(valid=True)
    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt123",
        "htmlLink": "https://calendar.google.com/evt123",
    }

    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("googleapiclient.discovery.build", return_value=fake_service),
    ):
        result = google_calendar.create_reminder_event("Wish brother happy birthday", "2026-08-28T09:00:00")

    assert result == {"event_id": "evt123", "html_link": "https://calendar.google.com/evt123"}
    _, kwargs = fake_service.events.return_value.insert.call_args
    body = kwargs["body"]
    assert body["summary"] == "Wish brother happy birthday"
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 0}, {"method": "email", "minutes": 0}],
    }
    # A real IANA zone name must be attached, not just a fixed UTC offset —
    # confirmed live as a real bug otherwise: Google Calendar rejects a
    # RECURRING event with only an offset-bearing dateTime ("400: Missing
    # time zone definition for start time"), since a fixed offset can't
    # describe how future occurrences behave across a DST transition.
    assert body["start"]["dateTime"] == "2026-08-28T09:00:00"
    assert body["start"]["timeZone"]
    assert body["end"]["timeZone"] == body["start"]["timeZone"]


def test_create_reminder_event_raises_when_not_authorized(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "missing_token.json")
    with pytest.raises(RuntimeError):
        google_calendar.create_reminder_event("Some reminder", "2026-08-28T09:00:00")


def test_build_rrule_daily():
    assert google_calendar._build_rrule("daily", "2026-08-27T09:00:00") == "RRULE:FREQ=DAILY"


def test_build_rrule_weekly_derives_byday_from_due_at_weekday():
    # 2026-08-31 is a Monday
    assert google_calendar._build_rrule("weekly", "2026-08-31T09:00:00") == "RRULE:FREQ=WEEKLY;BYDAY=MO"
    # 2026-08-27 is a Thursday
    assert google_calendar._build_rrule("weekly", "2026-08-27T09:00:00") == "RRULE:FREQ=WEEKLY;BYDAY=TH"


def test_build_rrule_monthly():
    assert google_calendar._build_rrule("monthly", "2026-08-27T09:00:00") == "RRULE:FREQ=MONTHLY"


def test_build_rrule_rejects_unknown_value():
    with pytest.raises(ValueError):
        google_calendar._build_rrule("yearly", "2026-08-27T09:00:00")


def test_create_reminder_event_with_recurrence_includes_rrule(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")

    fake_creds = MagicMock(valid=True)
    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt1", "htmlLink": "https://calendar.google.com/evt1"
    }

    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("googleapiclient.discovery.build", return_value=fake_service),
    ):
        google_calendar.create_reminder_event("Weekly standup", "2026-08-31T09:00:00", "weekly")

    _, kwargs = fake_service.events.return_value.insert.call_args
    assert kwargs["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]


def test_create_reminder_event_without_recurrence_omits_field(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")

    fake_creds = MagicMock(valid=True)
    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt1", "htmlLink": "https://calendar.google.com/evt1"
    }

    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("googleapiclient.discovery.build", return_value=fake_service),
    ):
        google_calendar.create_reminder_event("One-off", "2026-08-31T09:00:00")

    _, kwargs = fake_service.events.return_value.insert.call_args
    assert "recurrence" not in kwargs["body"]


def test_update_event_sends_only_changed_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")

    fake_creds = MagicMock(valid=True)
    fake_service = MagicMock()
    fake_service.events.return_value.patch.return_value.execute.return_value = {
        "id": "evt1", "htmlLink": "https://calendar.google.com/evt1"
    }

    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("googleapiclient.discovery.build", return_value=fake_service),
    ):
        result = google_calendar.update_event("evt1", due_at="2026-09-12T14:00:00")

    assert result == {"event_id": "evt1", "html_link": "https://calendar.google.com/evt1"}
    _, kwargs = fake_service.events.return_value.patch.call_args
    assert kwargs["eventId"] == "evt1"
    body = kwargs["body"]
    assert "summary" not in body
    assert body["start"]["dateTime"] == "2026-09-12T14:00:00"
    assert body["start"]["timeZone"]


def test_update_event_raises_when_not_authorized(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "missing_token.json")
    with pytest.raises(RuntimeError):
        google_calendar.update_event("evt1", text="New text")


def test_delete_event_swallows_api_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "token.json")
    google_calendar.TOKEN_PATH.write_text("{}")
    fake_creds = MagicMock(valid=True)

    with (
        patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=fake_creds),
        patch("googleapiclient.discovery.build", side_effect=Exception("boom")),
    ):
        google_calendar.delete_event("evt123")  # must not raise


def test_delete_event_no_op_when_not_authorized(monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "TOKEN_PATH", tmp_path / "missing_token.json")
    google_calendar.delete_event("evt123")  # must not raise
