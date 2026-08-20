# TEJAS — Personal AI Assistant

*An Indian AI voice assistant, just like JARVIS.*

TEJAS is a full-stack personal AI assistant with a sci-fi, JARVIS-style holographic interface. It runs entirely on your own machine by default (local LLM via Ollama, local speech-to-text via Whisper, local SQLite/ChromaDB memory), with optional cloud backends (Anthropic Claude, OpenAI Whisper) you can switch on when you want stronger reasoning or accuracy.

It talks back and forth by text or voice, remembers context across sessions, and can actually *do* things — search the web, check the weather, run Python, manage files in a sandbox, and track reminders — via a small, pluggable tool system.

## Features

- **Real-time chat** over a WebSocket, with streaming responses.
- **Voice input** — click the mic, speak, and it transcribes and sends automatically (local Whisper by default, no cloud dependency).
- **Voice output** — TEJAS speaks its replies aloud (toggle on/off in the top bar); works for both typed and spoken questions.
- **Tool use** — the model can call real tools (web search, weather, date/time, system info, sandboxed file I/O, sandboxed Python execution, reminders, fact memory) instead of guessing.
- **Session history** — chats persist in SQLite; pick up an old conversation, start a new one, or delete one you no longer need, Claude-style.
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

> The sidebar's Weather widget is **not** wired to the `get_weather` tool — it's an independent client-side fetch straight from Open-Meteo (`WeatherWidget.tsx`), separate from the backend's `tools/weather.py` that the AI calls when you *ask* about weather in chat. Both happen to hit the same free API, but changing one does not affect the other.

## Project structure

```
tejas-assistant/
├── server.py                # FastAPI app: WebSocket endpoint + REST endpoints
├── main.py                  # CLI entry point — text-only terminal chat, no web UI
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
- [Ollama](https://ollama.com) installed and running locally — this is the default LLM backend. `qwen2.5:7b` is the one active at startup (`OLLAMA_MODEL` in `.env`), but pulling all three lets you use the in-app model switcher (top bar) to swap between them live:
  ```
  ollama pull qwen2.5:7b
  ollama pull llama3.1
  ollama pull gemma2:9b
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

**CLI mode** — `main.py` is a separate, text-only terminal interface that talks to the same `Agent` class directly (no server, no browser, no voice):
```bash
python main.py            # new session
python main.py --resume   # continue the most recent session
python main.py --debug    # also print logs to stdout (always logged to assistant.log)
```

## Configuration

All settings live in `.env` (copy from `.env.example`). Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` (local, free) or `anthropic` (hosted, better tool-calling) |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local model name (must be pulled already) |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded after last use. Higher avoids reload cost between messages; see [Performance notes](#performance-notes) |
| `ANTHROPIC_API_KEY` | — | Required only if `LLM_PROVIDER=anthropic` |
| `ASSISTANT_MODEL` | `claude-sonnet-4-6` | Anthropic model name |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. Kept at 0 for factual reliability — small local models drift from facts given in the prompt at higher temperatures |
| `ASSISTANT_NAME` | `TEJAS` | Display name used in the UI and system prompt |
| `STT_PROVIDER` | `local` | `local` (faster-whisper, offline) or `openai` (Whisper API; auto-falls-back to local on failure) |
| `WHISPER_MODEL` | `small.en` | Local Whisper model size (`tiny.en` → `medium.en`, bigger = more accurate but slower) |
| `OPENAI_API_KEY` | — | Required only if `STT_PROVIDER=openai` |
| `SEARCH_API_KEY` | — | Optional. Enables `web_search` (free key at [tavily.com](https://tavily.com)) |

## Available tools

The model decides when to call these — it doesn't guess at things it can look up. Kept to 8 tools (not more) deliberately: on CPU-only local inference, every tool in the schema adds real, measured latency to *every* request (see [Performance notes](#performance-notes) below), so related actions are grouped into one tool with an operation/action parameter rather than split into many single-purpose ones.

| Tool | What it does |
|---|---|
| `web_search` | Search the web (Tavily) for current info not in the model's training data |
| `get_weather` | Current weather + 3-day forecast for a city (Open-Meteo, no key needed) |
| `get_current_datetime` | Date/time in a specific *other* timezone (the local system time is already given to the model directly, so this only covers timezone lookups) |
| `get_system_info` | Read-only OS/CPU/disk info about the host machine |
| `file_ops` | Read, write, or list files in a sandbox directory (`data/sandbox/`) — set `operation` to `read`/`write`/`list`. Can't touch the rest of your filesystem |
| `execute_python` | Runs a Python snippet in an isolated subprocess with a timeout |
| `manage_reminders` | Add, list, or complete reminders — set `action` to `add`/`list`/`complete` |
| `remember_fact` | Save a fact/preference about you for future recall |

To add a new tool: create a file in `tools/`, subclass `Tool` (see `tools/base.py`), and add one line to `tools/__init__.py`.

## API reference

The frontend talks to `server.py` over one WebSocket plus a handful of REST endpoints — useful if you're scripting against it directly or building a different frontend:

| Endpoint | Purpose |
|---|---|
| `WS /ws` | The chat itself. Send `{"text": "..."}`; receive a stream of `{"type": "chunk\|tool\|done\|error", ...}` events. Optional `?session_id=<id>` or `?resume=1` query params pick which session to attach to. |
| `GET /api/meta` | Assistant name, active model, and the full tool list (name + description) — what the UI header/status panel reads on load. |
| `GET /api/models` | All models in `AVAILABLE_MODELS` plus which one is currently active. |
| `POST /api/models` | Switch the active model. Body: `{"id": "<model id from /api/models>"}`. Takes effect on the next chat turn. |
| `GET /api/sessions` | Recent sessions with message counts, newest first. |
| `DELETE /api/sessions/{id}` | Delete a session and its messages. 404 if it doesn't exist. |
| `GET /api/reminders` | Active (not-yet-completed) reminders. |
| `POST /api/transcribe` | Multipart audio upload (`audio` field) → `{"text": "..."}`. Powers the mic button; also usable standalone. |

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

## Performance notes

On CPU-only hardware (no CUDA/ROCm GPU — the common case on a laptop), local models are slow for a specific, measured reason: **the tool schema sent on every request is the dominant cost, not model size or generation length.** Measured on an i5-1135G7 laptop (4 cores, integrated graphics only) with `qwen2.5:7b`:

| Call | Time |
|---|---|
| Cold model load + simple question, no tools | ~14s |
| Same question, warm, no tools | ~2s |
| Question, warm, **with** the full tool schema attached | ~42–57s |

That last number looks like it should be a fixed cost paid on every single request — but it isn't. **Ollama caches the KV state for a matching prompt prefix across separate calls**: a byte-identical repeat of the same tool-laden call dropped from 38s to ~4.5s on the 2nd/3rd try, and — more usefully — even a call with the *same system prompt + tools but a different trailing question* still dropped to ~12–15s, because the expensive, mostly-static part (system prompt + ~800 tokens of tool schema) got reused instead of reprocessed.

This app used to defeat that caching on every request without realizing it: the live clock was embedded directly inside the system prompt (`Current date and time: ...`), which changes every call by definition — meaning the "prefix" was never actually identical twice, so Ollama had nothing to reuse. Fixed by splitting it: `config.static_system_prompt()` (tool rules, model capabilities — never changes for a given model) is now the system message on every call, and `config.current_time_context()` (the live clock) is injected per-turn into the outgoing user message instead (`agent/loop.py`'s `_messages_for_llm`, the same non-persisted-enrichment pattern already used for recalled memory context) — so it grounds the model correctly without touching the cacheable prefix.

End-to-end impact, measured through the real app (not a synthetic benchmark) over a 4-turn conversation: response time went **131s → 47s → 31s → 30s** and leveled off there, instead of staying flat around 45–57s on every single turn as it did before. `vector.recall()`'s semantic-memory lookup and the real (longer, multi-paragraph) system prompt mean the real app doesn't hit the ~12–15s floor a stripped-down isolated test did, but the trend — declining then stabilizing, rather than flat — is the real win.

Two other things in this codebase help further:
- **`OLLAMA_KEEP_ALIVE`** (`.env`, default `30m`) keeps the model loaded in memory between messages so you don't pay the ~10s reload cost on every turn — Ollama's own default is only 5 minutes.
- **Tool count is kept to 8** (see [Available tools](#available-tools)) instead of one tool per action — `file_ops` and `manage_reminders` each replace what used to be 3 separate tools, cutting the schema payload ~20% and shaving a proportional amount off the one-time cost of populating the cache. Real trade-off: a multi-purpose tool with an `operation`/`action` parameter is marginally harder for a small local model to call correctly than several clearly-named single-purpose tools — verified live against `qwen2.5:7b` before landing (all 4 operations across both consolidated tools called correctly).

None of this changes the fundamental limit: the *first* processing of ~800 tokens of tool schema on a CPU-only 7B model will always take tens of seconds. If that's not acceptable, `LLM_PROVIDER=anthropic` (once you have a valid key) doesn't hit this wall at all — cloud inference processes the same schema in a couple of seconds, every time.

## Security notes

- Never commit your real `.env` — only `.env.example` (with placeholder values) belongs in version control.
- `execute_python` and `file_ops` are sandboxed to `data/sandbox/` — they cannot access the rest of your filesystem.
- `execute_python` runs with a hard timeout and no special privileges beyond the sandbox directory.
