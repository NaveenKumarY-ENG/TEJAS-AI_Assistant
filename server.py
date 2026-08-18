"""
FastAPI server exposing TEJAS over a WebSocket, plus the static web UI.

Run:  uvicorn server:app --reload
Then: http://127.0.0.1:8000
"""
import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import Agent, transcription
from config import AVAILABLE_MODELS, config
from memory import structured
from tools import ALL_TOOLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("assistant.server")

app = FastAPI(title=f"{config.assistant_name} API")

# The React/Three.js dashboard (frontend/) replaced the old static/ single-page
# UI. In dev, run `npm run dev` inside frontend/ instead — its Vite server
# proxies /api and /ws to this FastAPI process. This block only serves the
# built production bundle (`npm run build` in frontend/).
STATIC_DIR = Path(__file__).parent / "static"
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
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
async def meta():
    """Everything the UI needs to render its header and tool list."""
    return {
        "assistant_name": config.assistant_name,
        "model": config.active_model,
        "tools": [{"name": t.name, "description": t.description} for t in ALL_TOOLS],
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