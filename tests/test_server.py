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


def test_api_meta_reports_live_ai_status():
    """/api/meta is the one place the frontend learns whether Live.AI's
    tools are actually usable (see App.tsx's setLiveAiEnabled/
    setBrowserAvailable/setSearchConfigured) — same pattern as the
    pre-existing tts_available/ocr_available fields, just for the new
    open_website/read_webpage/shop_flipkart tools."""
    with patch.object(server.config, "live_ai_enabled", True), patch.object(
        server.browser, "available", return_value=True
    ), patch.object(server.config, "search_api_key", "tvly-realkey123"):
        client = TestClient(server.app)  # no `with` block: skips lifespan/startup hooks entirely
        response = client.get("/api/meta")
    body = response.json()
    assert body["live_ai_enabled"] is True
    assert body["browser_available"] is True
    assert body["search_configured"] is True
    assert any(t["name"] == "open_website" for t in body["tools"])
    assert any(t["name"] == "shop_flipkart" for t in body["tools"])


def test_api_meta_reports_live_ai_disabled_and_unconfigured():
    with patch.object(server.config, "live_ai_enabled", False), patch.object(
        server.browser, "available", return_value=False
    ), patch.object(server.config, "search_api_key", ""):
        client = TestClient(server.app)
        response = client.get("/api/meta")
    body = response.json()
    assert body["live_ai_enabled"] is False
    assert body["browser_available"] is False
    assert body["search_configured"] is False


def test_api_meta_reports_cloud_provider_configuration_as_booleans_only():
    """Settings' Privacy & Data / AI & Models sections read these — must
    never leak the actual key, only whether it looks like a real one."""
    with patch.object(server.config, "anthropic_api_key", "sk-ant-realkey123"), patch.object(
        server.config, "gemini_api_key", "AIzaReal-gemini-key"
    ):
        client = TestClient(server.app)
        response = client.get("/api/meta")
    body = response.json()
    assert body["anthropic_configured"] is True
    assert body["gemini_configured"] is True
    assert "sk-ant-realkey123" not in response.text
    assert "AIzaReal-gemini-key" not in response.text


def test_api_meta_reports_unconfigured_cloud_providers():
    with patch.object(server.config, "anthropic_api_key", "sk-ant-your-key-here"), patch.object(
        server.config, "gemini_api_key", ""
    ):
        client = TestClient(server.app)
        response = client.get("/api/meta")
    body = response.json()
    assert body["anthropic_configured"] is False
    assert body["gemini_configured"] is False


def test_system_health_reports_ok_when_everything_is_reachable():
    with patch.object(server.vector, "get_client") as mock_get_client:
        mock_get_client.return_value.heartbeat.return_value = 1234567890
        client = TestClient(server.app)
        response = client.get("/api/system/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["vector_store"] == "ok"
    assert body["python_version"]


def test_system_health_reports_degraded_without_crashing_when_sqlite_is_unreachable():
    """A real failure must report "unavailable," never crash the endpoint
    (or the app) and never silently claim "ok" — same defensive contract
    /api/meta already applies to tts_available/ocr_available."""
    with patch.object(server.sqlite3, "connect", side_effect=OSError("disk I/O error")), patch.object(
        server.vector, "get_client"
    ) as mock_get_client:
        mock_get_client.return_value.heartbeat.return_value = 1
        client = TestClient(server.app)
        response = client.get("/api/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"
    assert body["vector_store"] == "ok"


def test_system_health_reports_degraded_without_crashing_when_chromadb_is_unreachable():
    with patch.object(server.vector, "get_client", side_effect=RuntimeError("connection refused")):
        client = TestClient(server.app)
        response = client.get("/api/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["vector_store"] == "unavailable"


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
