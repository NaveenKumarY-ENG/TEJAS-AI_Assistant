"""
Tests for server.py's startup-time dashboard-launch logic and WebSocket
handler. Run with: pytest tests/
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import server


def test_open_dashboard_uses_a_daemon_timer_thread(tmp_path, monkeypatch):
    """Regression test for a real bug found live: this timer thread used to
    default to non-daemon. Combined with the old webbrowser.get(...).open()
    call (GenericBrowser.open() blocks on Popen(...).wait() until the
    launched Chrome PROCESS exits, not until the tab opens — see _open()'s
    comment in server.py), a stray non-daemon thread stuck waiting on a
    still-open Chrome window silently prevented the whole worker process
    from ever exiting. `uvicorn --reload` depends on the old process
    actually exiting to restart after a file change, so this permanently
    hung the server after the very first code edit. The thread must be a
    daemon so it can never block process shutdown, regardless of what its
    target function does."""
    monkeypatch.delenv("TEJAS_NO_AUTO_OPEN", raising=False)
    fake_timer = MagicMock()
    with patch("server.tempfile.gettempdir", return_value=str(tmp_path)), patch(
        "server.threading.Timer", return_value=fake_timer
    ) as mock_timer_cls:
        server._open_dashboard_once()

    mock_timer_cls.assert_called_once()
    assert fake_timer.daemon is True
    fake_timer.start.assert_called_once()


def test_open_dashboard_launches_chrome_without_waiting_for_it_to_exit(tmp_path, monkeypatch):
    """The actual fix: launching Chrome must not block on the launched
    process exiting (see the bug described in the test above) — it should
    fire the process and return immediately, not wait() on it."""
    monkeypatch.delenv("TEJAS_NO_AUTO_OPEN", raising=False)
    captured = {}

    def fake_timer_ctor(delay, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch("server.tempfile.gettempdir", return_value=str(tmp_path)), patch(
        "server.threading.Timer", side_effect=fake_timer_ctor
    ), patch("server._find_chrome", return_value=r"C:\fake\chrome.exe"), patch(
        "server.subprocess.Popen"
    ) as mock_popen, patch(
        "server.webbrowser.open"
    ) as mock_webbrowser_open:
        server._open_dashboard_once()
        captured["callback"]()  # run the actual _open() body

    mock_popen.assert_called_once_with([r"C:\fake\chrome.exe", server.DASHBOARD_URL])
    mock_webbrowser_open.assert_not_called()


def _fake_agent(**_kwargs):
    fake = MagicMock()
    fake.session_id = 1
    fake.history = []

    def fake_chat_streaming(user_input, on_chunk, on_tool=None, on_tool_result=None):
        on_chunk(f"echo: {user_input}")
        return f"echo: {user_input}"

    fake.chat_streaming.side_effect = fake_chat_streaming
    return fake


def test_ws_survives_a_malformed_frame_without_dropping_the_connection():
    """Regression test for a real risk found during review: an unguarded
    json.loads()/payload.get() on an incoming WS frame meant a single
    malformed frame (not valid JSON, or JSON that isn't an object, e.g.
    a bare "42") raised out of the handler entirely — not a
    WebSocketDisconnect, so uncaught by the except below it — abnormally
    tearing down the whole connection instead of just that one bad frame.
    The shipped frontend always sends well-formed frames, so this isn't
    reachable through normal UI use, but the socket must not die over one
    bad frame from any other client."""
    with patch("server.Agent", side_effect=_fake_agent):
        client = TestClient(server.app)  # no `with` block: skips lifespan/startup hooks entirely
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # the initial "ready" event

            ws.send_text("not valid json")
            error_event = ws.receive_json()
            assert error_event["type"] == "error"

            # The connection must still be alive and usable afterward.
            ws.send_text(json.dumps({"text": "hello"}))
            chunk = ws.receive_json()
            assert chunk == {"type": "chunk", "text": "echo: hello"}
            assert ws.receive_json()["type"] == "done"
