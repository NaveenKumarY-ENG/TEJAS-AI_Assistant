"""
FastAPI server exposing TEJAS over a WebSocket, plus the React/Three.js
dashboard (frontend/).

Run:  uvicorn server:app --reload
Then: http://127.0.0.1:8000 (opens automatically in Chrome — set
      TEJAS_NO_AUTO_OPEN=1 to disable)

The dashboard must be built first (`cd frontend && npm install && npm run
build`) — see README.md. There is deliberately no fallback UI: an older
plain-HTML version of this app used to live in static/ and get served
silently whenever frontend/dist was missing, which meant a fresh clone that
skipped the frontend build looked like a completely different, much older
app with no explanation why. That fallback has been removed; if the build
is missing, "/" now returns a clear error instead of a different UI.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import Agent, transcription, tts
from config import AVAILABLE_MODELS, AVAILABLE_TTS_VOICES, config
from memory import structured
from tools import ALL_TOOLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("assistant.server")

app = FastAPI(title=f"{config.assistant_name} API")

DASHBOARD_URL = "http://127.0.0.1:8000"


def _find_chrome() -> str | None:
    """Best-effort path to a real Chrome executable. Python's stdlib
    `webbrowser` module can't reliably find Chrome by name on Windows (it has
    no built-in "chrome" alias there, unlike some Unix setups), so this
    checks the registry entry Chrome's installer registers, then the two
    common install locations, before giving up and letting the caller fall
    back to the OS default browser."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            ) as key:
                path = winreg.QueryValueEx(key, None)[0]
                if path and Path(path).exists():
                    return path
        except OSError:
            pass
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            if Path(candidate).exists():
                return candidate
    else:
        import shutil

        for name in ("google-chrome", "chrome", "chromium"):
            found = shutil.which(name)
            if found:
                return found
    return None


def _open_dashboard_once() -> None:
    """Opens the dashboard in Chrome when the server starts — but only once
    per `uvicorn --reload` session, not on every autoreload restart (which
    would otherwise pop a new tab on every file save).

    `--reload` runs this app in a fresh child process on each restart (see
    uvicorn's BaseReload.restart), so an in-memory flag here wouldn't survive
    a restart — but the reloader process that spawns those children keeps
    the same PID for the whole session, so a temp marker file keyed on
    os.getppid() correctly recognizes "already opened this session" across
    restarts, while a genuinely fresh `uvicorn` invocation (new reloader
    PID) still opens a tab. Set TEJAS_NO_AUTO_OPEN=1 to disable entirely
    (e.g. headless/CI environments).
    """
    if os.environ.get("TEJAS_NO_AUTO_OPEN"):
        return

    marker = Path(tempfile.gettempdir()) / "tejas_assistant_browser_session.pid"
    session_pid = str(os.getppid())
    try:
        if marker.exists() and marker.read_text().strip() == session_pid:
            return
        marker.write_text(session_pid)
    except OSError:
        pass  # best-effort — worst case an extra tab opens on a reload

    def _open() -> None:
        chrome_path = _find_chrome()
        if chrome_path:
            try:
                webbrowser.get(f'"{chrome_path}" %s').open(DASHBOARD_URL)
                return
            except webbrowser.Error:
                pass
        webbrowser.open(DASHBOARD_URL)  # fall back to whatever the OS default browser is

    # Give uvicorn a moment to actually start accepting connections before
    # the tab loads — the startup event fires just before that, not after.
    threading.Timer(1.0, _open).start()


@app.on_event("startup")
async def _launch_browser() -> None:
    _open_dashboard_once()


@app.on_event("startup")
async def _warm_up_tts() -> None:
    # Background thread, not awaited: even the "just check it's installed"
    # probe in agent/tts.py's available() can take several seconds (`import
    # torch` alone routinely does, from CUDA library loading), so this runs
    # concurrently with startup instead of delaying it — and, in the normal
    # case, finishes before any browser tab's first /api/meta request
    # arrives, so that request sees an instant cache hit rather than paying
    # the import cost itself.
    threading.Thread(target=tts.warm_up, daemon=True).start()

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(FRONTEND_DIST / "favicon.svg")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def index():
        return HTMLResponse(
            "<pre style='font:14px monospace;padding:2rem;line-height:1.6'>"
            "frontend/dist not found — the dashboard hasn't been built yet.\n\n"
            "Run this once:\n"
            "  cd frontend\n"
            "  npm install\n"
            "  npm run build\n\n"
            "Then restart the server (or refresh, if using --reload).\n"
            "See README.md for full setup steps.</pre>",
            status_code=503,
        )


@app.get("/api/meta")
async def meta():
    """Everything the UI needs to render its header and tool list."""
    # tts.available() is cached after its first real computation (see
    # agent/tts.py) and the startup hook warms that cache in a background
    # thread — but if a request races ahead of that thread finishing, the
    # underlying `import torch` it falls back to computing synchronously
    # can itself take several seconds. asyncio.to_thread keeps that race's
    # worst case from blocking the whole event loop (and the active /ws
    # chat with it), matching the same pattern /api/tts already uses.
    tts_available = await asyncio.to_thread(tts.available)
    return {
        "assistant_name": config.assistant_name,
        "model": config.active_model,
        "tools": [{"name": t.name, "description": t.description} for t in ALL_TOOLS],
        "tts_available": tts_available,
    }


class ModelSelection(BaseModel):
    id: str


@app.get("/api/models")
async def list_models():
    """Every model the UI can switch to, plus which one is active right now."""
    return {"models": AVAILABLE_MODELS, "active": config.active_model_id}


@app.post("/api/models")
async def select_model(selection: ModelSelection):
    """Switch the active LLM — takes effect on the next chat turn, no
    restart needed (agent/llm_client.py reads config fresh every call)."""
    try:
        config.set_active_model(selection.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"active": config.active_model_id, "model": config.active_model}


@app.get("/api/tts/voices")
async def list_tts_voices():
    """Every neural TTS voice the UI can switch to, plus which one is
    active right now (mirrors /api/models)."""
    return {"voices": AVAILABLE_TTS_VOICES, "active": config.tts_voice}


class TtsVoiceSelection(BaseModel):
    id: str


@app.post("/api/tts/voices")
async def select_tts_voice(selection: TtsVoiceSelection):
    """Switch the active neural voice — takes effect on the next /api/tts
    call, no restart needed (agent/tts.py reads config fresh every call)."""
    try:
        config.set_active_tts_voice(selection.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"active": config.tts_voice}


@app.get("/api/sessions")
async def sessions():
    return {"sessions": structured.list_sessions(limit=20)}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int):
    deleted = structured.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}


@app.get("/api/reminders")
async def reminders():
    return {"reminders": structured.list_reminders()}


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Speech-to-text for voice input — see agent/transcription.py."""
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="No audio received")
    try:
        text = await asyncio.to_thread(transcription.transcribe, data)
    except Exception:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail="Transcription failed")
    return {"text": text}


class TtsRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def synthesize_speech(payload: TtsRequest):
    """Neural voice output — see agent/tts.py. The frontend only calls this
    when /api/meta reported tts_available=true; it falls back to the
    browser's own TTS on any failure here rather than losing audio."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    try:
        audio = await asyncio.to_thread(tts.synthesize, text)
    except Exception:
        logger.exception("TTS synthesis failed")
        raise HTTPException(status_code=500, detail="TTS synthesis failed")
    return Response(content=audio, media_type="audio/wav")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    resume = websocket.query_params.get("resume") == "1"
    session_param = websocket.query_params.get("session_id")
    session_id = int(session_param) if session_param and session_param.isdigit() else None

    agent = Agent(session_id=session_id, resume=resume)
    logger.info(
        "Client connected (session %d, resume=%s, requested_session_id=%s)",
        agent.session_id, resume, session_id,
    )

    history = [m for m in agent.history if m["role"] in ("user", "assistant") and m["content"]]
    await websocket.send_json(
        {"type": "ready", "session_id": agent.session_id, "history": history}
    )

    loop = asyncio.get_running_loop()

    try:
        while True:
            raw = await websocket.receive_text()
            payload = json.loads(raw)
            user_input = (payload.get("text") or "").strip()
            if not user_input:
                continue

            # The agent is blocking (Ollama SDK is sync), so run it in a thread
            # and hand events back to the event loop as they occur.
            def send(event: dict):
                asyncio.run_coroutine_threadsafe(websocket.send_json(event), loop)

            def run():
                try:
                    agent.chat_streaming(
                        user_input,
                        on_chunk=lambda piece: send({"type": "chunk", "text": piece}),
                        on_tool=lambda name: send({"type": "tool", "name": name}),
                    )
                    send({"type": "done"})
                except Exception as e:
                    logger.exception("Error during chat turn")
                    send({"type": "error", "message": str(e)})

            await asyncio.to_thread(run)

    except WebSocketDisconnect:
        logger.info("Client disconnected (session %d)", agent.session_id)