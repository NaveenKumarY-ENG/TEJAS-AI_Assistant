# TEJAS — Personal AI Assistant

*An Indian AI voice assistant, just like JARVIS.*

TEJAS is a full-stack personal AI assistant with a sci-fi, JARVIS-style holographic interface. It runs entirely on your own machine by default (local LLM via Ollama, local speech-to-text via Whisper, local SQLite/ChromaDB memory), with optional cloud backends (Anthropic Claude, OpenAI Whisper) you can switch on when you want stronger reasoning or accuracy.

It talks back and forth by text or voice, remembers context across sessions, and can actually *do* things — search the web, check the weather, run Python, manage files in a sandbox, and track reminders — via a small, pluggable tool system.

## Features

- **Real-time chat** over a WebSocket, with streaming responses.
- **Voice input** — click the mic, speak, and it transcribes and sends automatically (local Whisper by default, no cloud dependency).
- **Voice output** — TEJAS speaks its replies aloud (toggle on/off in the top bar); works for both typed and spoken questions.
- **Tool use** — the model can call real tools (web search, weather, date/time, system info, sandboxed file I/O, sandboxed Python execution, reminders, fact memory) instead of guessing.
- **Session history** — chats persist in SQLite; pick up an old conversation or start a new one, Claude-style.
- **Semantic memory** — relevant snippets from past conversations are recalled automatically via a ChromaDB vector store (with safeguards so stale, point-in-time facts like "today's weather" never get recalled as if still true).
- **Switchable LLM backends** — local Ollama (Qwen 2.5, Llama 3.1, Gemma 2, ...) or Anthropic Claude (hosted, stronger tool-calling), swappable live from a dropdown in the top bar, no restart needed.
- **Dual STT backends** — local faster-whisper (free, offline) or OpenAI's Whisper API, with automatic fallback to local if the cloud call fails.
- **Quick actions** — one-click canned prompts (weather, web search, system check, reminders, memory, timezone lookups, quick calculations) in the sidebar for instant access without typing.
- **A hand-built Three.js hologram** — the "AI Core" visual reacts to the assistant's state (idle / listening / thinking / speaking).

## Architecture

```
Browser (React + Three.js) ⇄ WebSocket /ws ⇄ FastAPI (server.py) ⇄ Agent loop (agent/loop.py)
                                                                         │
                                                          ┌──────────────┼──────────────┐
                                                          │              │              │
                                                     LLM client      Tools          Memory
                                                (Ollama / Anthropic)  (tools/)  (SQLite + ChromaDB)
```

- **Backend**: Python, FastAPI, a single WebSocket endpoint that streams the conversation turn by turn. The agent loop calls the LLM, executes any tool calls it requests, feeds results back, and repeats until it produces a final answer.
- **Frontend**: React 19 + TypeScript + Vite, with a Three.js/`@react-three/fiber` hologram rendered full-bleed behind a floating chat UI, styled with Tailwind CSS v4 and state managed by Zustand.
- **Persistence**: SQLite for chat sessions/messages/reminders/facts (`memory/structured.py`), ChromaDB for semantic recall (`memory/vector.py`).

## Project structure

```
tejas-assistant/
├── server.py                # FastAPI app: WebSocket endpoint + REST endpoints
├── config.py                # All settings, loaded from .env
├── agent/
│   ├── loop.py               # Core agent loop: history, tool-calling, streaming, memory
│   ├── llm_client.py         # Ollama + Anthropic backends behind one interface
│   └── transcription.py      # Local (faster-whisper) + OpenAI speech-to-text
├── tools/                   # One file per tool; register new ones in tools/__init__.py
│   ├── web_search.py         # Tavily-backed web search
│   ├── weather.py            # Open-Meteo current weather + forecast
│   ├── datetime_tool.py      # Other-timezone date/time lookups
│   ├── system_info.py        # Read-only host OS/CPU/disk info
│   ├── file_ops.py           # Sandboxed read/write/list files
│   ├── code_exec.py          # Sandboxed Python execution
│   └── memory_tool.py        # Reminders + user-fact memory
├── memory/
│   ├── structured.py         # SQLite: sessions, messages, reminders, facts
│   └── vector.py             # ChromaDB: semantic recall of past conversations
├── frontend/                 # React/Three.js dashboard (Vite)
│   └── src/
│       ├── components/       # core/ (hologram), ui/ (chat, widgets), layout/, voice/
│       ├── hooks/             # WebSocket, speech recognition/synthesis, toast
│       └── store/             # Zustand app state
├── tests/                    # pytest suite (tools + LLM client translation logic)
├── data/                     # SQLite DB, vector store, sandbox dir (created at runtime)
└── .env.example              # Copy to .env and fill in
```

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the frontend)
- [Ollama](https://ollama.com) installed and running locally, with a model pulled — this is the default LLM backend:
  ```
  ollama pull qwen2.5:7b
  ```

## Setup

**1. Backend**

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env          # Windows: copy, macOS/Linux: cp
```

Edit `.env` as needed (see [Configuration](#configuration) below) — the defaults work out of the box with just Ollama running locally.

**2. Frontend**

```bash
cd frontend
npm install
```

## Running

You need both processes running at once, in separate terminals:

**Backend** (from the project root):
```bash
uvicorn server:app --reload
```

**Frontend** (from `frontend/`, dev mode with hot reload):
```bash
npm run dev
```

Then open the URL Vite prints (typically `http://localhost:5173`). The Vite dev server proxies `/api` and `/ws` to the FastAPI backend on port 8000.

**Production build** — `npm run build` inside `frontend/` produces `frontend/dist`, which `server.py` serves directly (so you only need to run `uvicorn server:app` and open `http://127.0.0.1:8000`, no separate frontend server needed).

> **On every machine you run this on** (including a fresh `git clone`), you must do one of the two above — either `npm run dev` or `npm run build` — before opening the app. `frontend/dist` is gitignored (it's a build artifact, not source), so it does not exist right after cloning. If you open `http://127.0.0.1:8000` before building, the server now returns a clear "run `npm run build`" message rather than any UI at all — there is no fallback UI to fall back to. (An older, much plainer HTML/CSS/JS prototype used to live in `static/` and get served silently whenever the build was missing, which is why the UI could look completely different across machines — that prototype has been removed for exactly this reason.)

## Configuration

All settings live in `.env` (copy from `.env.example`). Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` (local, free) or `anthropic` (hosted, better tool-calling) |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local model name (must be pulled already) |
| `ANTHROPIC_API_KEY` | — | Required only if `LLM_PROVIDER=anthropic` |
| `ASSISTANT_MODEL` | `claude-sonnet-4-6` | Anthropic model name |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. Kept at 0 for factual reliability — small local models drift from facts given in the prompt at higher temperatures |
| `ASSISTANT_NAME` | `TEJAS` | Display name used in the UI and system prompt |
| `STT_PROVIDER` | `local` | `local` (faster-whisper, offline) or `openai` (Whisper API; auto-falls-back to local on failure) |
| `WHISPER_MODEL` | `base.en` | Local Whisper model size (`tiny.en` → `medium.en`, bigger = more accurate but slower) |
| `OPENAI_API_KEY` | — | Required only if `STT_PROVIDER=openai` |
| `SEARCH_API_KEY` | — | Optional. Enables `web_search` (free key at [tavily.com](https://tavily.com)) |

## Available tools

The model decides when to call these — it doesn't guess at things it can look up:

| Tool | What it does |
|---|---|
| `web_search` | Search the web (Tavily) for current info not in the model's training data |
| `get_weather` | Current weather + 3-day forecast for a city (Open-Meteo, no key needed) |
| `get_current_datetime` | Date/time in a specific *other* timezone (the local system time is already given to the model directly, so this only covers timezone lookups) |
| `get_system_info` | Read-only OS/CPU/disk info about the host machine |
| `read_file` / `write_file` / `list_files` | File I/O confined to a sandbox directory (`data/sandbox/`) — can't touch the rest of your filesystem |
| `execute_python` | Runs a Python snippet in an isolated subprocess with a timeout |
| `add_reminder` / `list_reminders` / `complete_reminder` | Persisted reminders |
| `remember_fact` | Save a fact/preference about you for future recall |

To add a new tool: create a file in `tools/`, subclass `Tool` (see `tools/base.py`), and add one line to `tools/__init__.py`.

## Testing

```bash
python -m pytest tests/ -q
```

Frontend build/lint:
```bash
cd frontend
npm run build
npm run lint
```

## Notes on accuracy

Local 7B-class models are noticeably less reliable than hosted models like Claude at tool-calling and staying grounded in given context. Two things in this codebase specifically compensate for that:

- The current date/time is injected directly into the system prompt every request (not left to the model to "know" or fetch), and sampling temperature is kept at 0 so the model doesn't drift off given facts.
- Point-in-time facts (weather, date/time, system stats) are excluded from long-term semantic memory, so a stale reading never gets recalled later and repeated as if still current.

If you need stronger overall reasoning and tool reliability, switch `LLM_PROVIDER=anthropic` in `.env` (costs money, requires an API key, no local install needed).

### Switching models at runtime

`OLLAMA_MODEL`/`LLM_PROVIDER` in `.env` only set the model active at startup. From there, the model switcher in the top bar of the UI lets you swap between the presets in `AVAILABLE_MODELS` (`config.py`) — currently `qwen2.5:7b`, `llama3.1:latest`, `gemma2:9b`, and Claude — on the fly, no restart or reconnect needed (`GET`/`POST /api/models`). Local models must already be pulled (e.g. `ollama pull llama3.1`); switching to the Anthropic entry requires `ANTHROPIC_API_KEY` to be set.

Not every local model supports Ollama's tool-calling template — `gemma2:9b` doesn't, and would error on every turn if tools were sent to it. Models like this are flagged `"supports_tools": False` in `AVAILABLE_MODELS`, which makes the backend omit the tools payload and adjust the system prompt accordingly: the model still chats normally, it just can't call `web_search`, `execute_python`, reminders, etc. while active. `qwen2.5:7b` and `llama3.1:latest` both support tools fully.

## Security notes

- Never commit your real `.env` — only `.env.example` (with placeholder values) belongs in version control.
- `execute_python`, `read_file`, `write_file`, and `list_files` are sandboxed to `data/sandbox/` — they cannot access the rest of your filesystem.
- `execute_python` runs with a hard timeout and no special privileges beyond the sandbox directory.
