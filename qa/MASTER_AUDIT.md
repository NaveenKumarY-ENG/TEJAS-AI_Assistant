# TEJAS — Master QA / Regression / Security Audit

Living document for the full audit requested 2026-08-31. Spans multiple
sessions — **read this file first when resuming this work.** Update it as
findings land; don't let results live only in chat transcripts.

Scope confirmed with the user: attempt the full 50-section directive across
as many sessions as it takes. The "Samsung Galaxy S25+" example in the
original directive was confirmed **illustrative**, not a real observed bug —
tested defensively as a template case, not as reproduction of a known
regression.

## How to resume this work

1. Read this file top to bottom.
2. Check the "Bug Tracker" for anything still OPEN.
3. Check "Section Status" for the next unstarted/in-progress section.
4. Re-run `pytest tests/ -q` first — confirm the baseline is still green
   before adding anything new.

---

## Architecture map (Phase 0 — discovery)

Confirmed by reading the actual code, not assumed.

| Layer | Files | Notes |
|---|---|---|
| Backend entry | `server.py` (556 lines) | FastAPI, REST + `/ws` WebSocket. 24 endpoints. |
| Agent core | `agent/loop.py` (425 lines) | `Agent` class — session load, tool loop, memory injection, streaming |
| LLM abstraction | `agent/llm_client.py` (413 lines) | Multi-provider: Ollama (local), Anthropic, Gemini |
| Voice | `agent/transcription.py`, `agent/tts.py` | faster-whisper STT, Kokoro-82M TTS |
| Config | `config.py` (273 lines) | **Module-level singleton** — see Finding F-001 |
| Tools | `tools/*.py` (13 files, 12 registered) | See tool inventory below |
| Memory (structured) | `memory/structured.py` | SQLite — sessions, messages, reminders, facts, documents |
| Memory (semantic) | `memory/vector.py` | ChromaDB — **global, not session-scoped**, by design (single-user app) |
| Knowledge/RAG | `memory/knowledge.py`, `memory/extraction.py`, `memory/ocr.py`, `memory/folder_watch.py` | PDF/DOCX/image ingestion, chunking, retrieval |
| Amazon integration | `integrations/browser.py` | Playwright, single persistent Chromium profile, global singleton context |
| Calendar integration | `integrations/google_calendar.py` | Real Google Calendar OAuth |
| Frontend | `frontend/src/` (46 .ts/.tsx files) | React + Three.js hologram, no automated frontend tests exist |
| Tests | `tests/*.py` (17 files, 224 tests as of last run) | Good coverage on tools/memory/knowledge; thin on server.py (3 tests) and agent/loop.py itself (no dedicated test_loop equivalent — verify) |

### Tool inventory (12 registered, `tools/__init__.py`)

| Tool | File | Side effects? |
|---|---|---|
| `web_search` | web_search.py | Read-only (external) |
| `get_weather` | weather.py | Read-only (external) |
| `get_current_datetime` | datetime_tool.py | Read-only |
| `get_system_info` | system_info.py | Read-only (local) |
| `file_ops` | file_ops.py | **Writes** — sandboxed dir only |
| `execute_python` | code_exec.py | **Executes code** — subprocess, 10s timeout, sandboxed |
| `manage_reminders` | memory_tool.py (`RemindersTool`) | **Writes** — SQLite + real Google Calendar |
| `remember_fact` | memory_tool.py (`RememberFactTool`) | **Writes** — vector store |
| `search_knowledge` | knowledge_tool.py | Read-only |
| `shop_amazon` | shopping_tool.py | Opens real browser, read-only on Amazon |
| `order_amazon` | order_tool.py | **Writes** — real Amazon cart, real checkout navigation (never purchase) |
| `view_amazon_cart` | cart_tool.py | Read-only |

### Key architectural findings (Phase 0)

- **F-001 (P3, informational):** `config` (`config.py`) is a process-wide
  singleton. `set_active_model`/`set_active_tts_voice` mutate it directly —
  model/voice selection is **global to the server process, not per-session
  or per-WebSocket-connection**. Two concurrent browser tabs/sessions share
  one active model. For a genuinely single-user, single-active-session app
  this is a reasonable simplification, not a bug — but it's a real
  near-miss: during the previous QA round, `POST /api/models` was called
  against the live server while the user's real session (294) was
  potentially active, briefly changing the model their own conversation
  would use, before being reverted. **Recommendation:** document this
  behavior in README (multi-tab caveat) rather than architecturally fix it,
  unless the user actually uses multiple simultaneous tabs/devices.
- **F-002 (not a bug, confirmed by design):** `memory/vector.py`'s
  `remember()`/`recall()` take no session parameter — semantic memory is
  global across all conversations. This is the Section 29 "Journey 9 Memory
  Isolation" test's premise (session A's data must never leak to session B)
  **inverted**: for TEJAS, recalling facts from a *different* past session
  is the intended feature (like ChatGPT's memory), not leakage. Re-scoped
  this test below to what's actually meaningful for a single-user app:
  recall *quality* (does something semantically irrelevant get pulled in
  and cause confusion) rather than recall *isolation*.
- **F-003 (already fixed, confirmed in code):** `agent/loop.py` has three
  separate, well-documented guards against exactly the "memory poisoning by
  stale data" failure mode Section 11 warns about — volatile tool results
  (weather/datetime/system-info), knowledge-base listings, and reminder
  listings are all excluded from `vector.remember()`, each with a comment
  citing the live bug that motivated it. This is strong existing evidence
  the team (prior session work) already fought this exact class of bug.
- **F-004:** `integrations/browser.py`'s Playwright context is a single
  global singleton, matching F-001/F-002's pattern — consistent
  single-user-app architecture throughout, not an inconsistency.

---

## Section status

Legend: ⬜ not started · 🔄 in progress · ✅ done this session · ⏭️ deferred (documented why)

| § | Topic | Status | Notes |
|---|---|---|---|
| 2-3 | Discovery / architecture map | ✅ | See above |
| 4 | Critical shopping bug (Samsung S25+) | ✅ | Confirmed illustrative; ran live defensive test — found B-001 (variant substitution, fixed) and B-005 (tool routing, open) |
| 5-9 | Shopping truthfulness / variant / price / link / cart validation | 🔄 | B-001 fixed this session; building on already-extensive prior-session order_tool.py/shopping_tool.py/cart_tool.py coverage |
| 10 | PDF/knowledge/shopping parsing separation | ⬜ | |
| 11 | Memory audit | 🔄 | F-002/F-003 above; recall-quality test pending |
| 12 | Vector DB cleanup | ⬜ | |
| 13 | RAG quality | ✅ (partial) | Found + fixed B-004 (stale relevance threshold) live; existing 36 tests (35+1 new) — broader ambiguous/conflicting-document scenarios (§13) still untested |
| 14 | OCR | ⬜ | |
| 15-16 | Multi-model / routing | 🔄 | Found + fixed B-002 (Anthropic SDK crash, was 100% broken); found B-003 (bad API key, needs user); confirmed Gemini healthy on a simple prompt; qwen2.5:7b (default) has the open B-005 tool-routing issue |
| 17 | Tool-calling exhaustive negative testing | ⬜ | |
| 18 | Agent loop testing | ⬜ | |
| 19 | Frontend regression | ⬜ | No automated frontend tests exist (confirmed prior session) — manual only |
| 20 | Backend endpoint testing | 🔄 | Partial coverage from prior QA round (reminders/knowledge/models/tts endpoints) |
| 21 | Security testing | ⬜ | |
| 22 | Negative testing (general) | 🔄 | Partial from prior round |
| 23-24 | Truthfulness / hallucination adversarial suite | ⬜ | |
| 25 | Cache audit | ⬜ | Need to find what's actually cached first |
| 26 | Performance | ⬜ | |
| 27 | Resource management | ⬜ | F-004-adjacent: browser pages never `.close()`'d — noted prior session as deliberate (user-facing tabs), revisit for `view_amazon_cart`'s read-only case |
| 28-29 | Automated E2E journeys | ⬜ | |
| 30 | Regression matrix | 🔄 | Being built as sections complete |
| 31 | Anti-cheating check | — | Standing principle, not a one-time task |
| 32-34 | Code/config/dependency quality review | ⬜ | |
| 35-36 | Observability / error handling standard | ⬜ | |
| 37 | Test data hygiene | ✅ | Prior QA round already cleaned all test reminders/notes/sessions |
| 38 | AI eval dataset (375+ prompts) | ⬜ | Large — will build incrementally, not fabricate a one-shot "ran 375 prompts" claim |
| 39-42 | Quality gates / bug severity / RCA | 🔄 | This document *is* the tracker |
| 43-44 | Acceptance tests (Samsung S25+, Legion keyboard cover) | 🔄 | Running now |
| 45 | Clean-state test | ⬜ | |
| 46-47 | Final validation / report | ⬜ | Not until the above is substantially real |

---

## Bug Tracker

| ID | Sev | Component | Problem | Status |
|---|---|---|---|---|
| B-001 | P1 | `tools/order_tool.py` `_best_match` | Name-based ordering silently substitutes a wrong product **variant** (color/RAM/storage) when the exact requested attribute isn't among search results, with no disclosure. Live-reproduced: "Samsung Galaxy S25+ ... (Silver Shadow, 12GB RAM, 256GB Storage)" against 5 real Amazon.in results (Icy Blue, Titanium Silver, Titanium JetBlack x2 dupes, Black — no Silver Shadow present) picked "Titanium Silver" (0.680 whole-string similarity, highest of the field) and would have added it to cart with no mention that the color didn't match. Root cause: `difflib.SequenceMatcher` whole-string similarity has no concept of "does this satisfy the specific attribute the user named" — a coincidental substring overlap ("Silver" inside "Titanium Silver") outscores genuinely different variants that happen to have longer titles. Directly matches Section 6's variant-validation requirement. | **FIXED** — `_requested_attributes`/`_unmatched_attributes` added, `run()` now refuses to proceed and shows real alternatives instead of substituting. 4 new tests, verified doesn't reach the browser on mismatch. |
| B-002 | P1 | `agent/llm_client.py` `_anthropic_chat`/`_anthropic_chat_streaming` | Anthropic provider was **completely broken** — every single call crashed with `TypeError: Messages.create()/stream() got an unexpected keyword argument 'temperature'`. Live-reproduced by switching the live server to `anthropic:claude-sonnet-4-6` and sending a real message. Root cause: `requirements.txt` pinned only a floor (`anthropic>=0.40.0`), so a fresh install pulled in SDK 1.0.0, whose `Messages.create()`/`.stream()` dropped `temperature` from their typed signatures entirely (confirmed via `inspect.signature` against the installed package). | **FIXED** — moved to `extra_body={"temperature": ...}` (confirmed live: request now actually reaches the API, see B-002-related finding below). `requirements.txt` now pins `anthropic>=1.0.0,<2.0.0`. 2 new regression tests using `create_autospec` against the real SDK class (not a bare `MagicMock`, which would never have caught this) — verified these tests actually fail against the old broken call shape before confirming the fix. |
| B-003 | P2 | `.env` / credentials (not code) | The configured `ANTHROPIC_API_KEY` is rejected by Anthropic's API with `401 authentication_error: API key is invalid`, discovered while verifying B-002's fix reached the real API. This is an environment/credentials issue, not a code bug — flagging for the user to check (key may be expired, rotated, or mistyped in `.env`). Anthropic provider cannot be fully end-to-end verified until this is resolved on the user's end. | **Needs user action** — not something I can fix myself |
| B-004 | P2 | `memory/knowledge.py` `_MAX_RELEVANT_DISTANCE` | RAG relevance threshold (1.6) was stale/too permissive for the current knowledge-base contents — live-reproduced: a completely unrelated Amazon shopping query scored distance 1.417-1.648 (below the 1.6 cutoff), so AI/ML document content ("advancements in deep learning...") got injected as "relevant knowledge-base content" into a phone-shopping conversation on every turn. Freshly measured: genuinely relevant queries land at 0.61-0.73, unrelated ones at 1.64-1.89 for the current collection — the old threshold sat inside the unrelated cluster. Plausibly contributed to B-005's confusion (irrelevant context in the prompt). | **FIXED** — tightened to 1.2 (documented as collection-dependent, re-verify if it drifts again as documents change). 1 new regression test reproducing the exact live case. |
| B-005 | P1 | Tool routing (local model, `qwen2.5:7b`) | For a specific, real, verbose phrasing — "Add [Product] ([Color], [RAM], [Storage]), [Camera] to cart." — the active local model never called `order_amazon` at all; it called only `shop_amazon` (search) and free-formed unsolicited accessory recommendations instead, no cart action, no error surfaced. Reproduced 3x consistently. A static system-prompt rule alone did NOT fix it. | **FIXED** — `_is_cart_request_query`/`_CART_REQUEST_RE` added to `agent/loop.py`, injecting a strong per-turn directive right next to the user's message (same fix shape already proven for the analogous reminder-tool-skipping bug in this same file) rather than relying on the static system prompt alone. 4 new tests. **Live end-to-end verified**: re-ran the exact original failing request — `order_amazon` was called, and — checked via the *actual* cart state (`view_amazon_cart`), not the narrated reply — the genuinely correct product (exact color/RAM/storage, real ₹89,999.00 price) is really in the cart. |

**Root cause analysis — B-001 (why it wasn't caught before):** `_best_match` was added in the previous session's "order by product name" feature and was live-tested only with product names that had NO color ambiguity in the actual top results (iQOO Z11, OnePlus Nord CE6) — the happy path where the top result already matched every attribute. The failure mode (best-available match ≠ requested variant) was never exercised because no prior test constructed a result set where the requested attribute was genuinely absent.

**Root cause analysis — B-002 (why it wasn't caught before):** Existing `test_llm_client.py` coverage only tested the pure data-transformation helpers (`_anthropic_messages`, `_anthropic_tools`, `_parse_anthropic_message`) — nothing exercised the actual SDK call. A prior test using a bare `MagicMock()` for the client would have silently accepted the broken `temperature=` kwarg and never caught this; only `create_autospec` against the real SDK class enforces the real signature. Prevention: the 2 new tests use exactly that mechanism.

---

## Regression matrix

| Feature | Unit | Integration | E2E | AI Eval | Negative | Status |
|---|---|---|---|---|---|---|
| Chat | ⬜ | ✅ (prior round) | ⬜ | ⬜ | ✅ (prior round) | |
| RAG/Knowledge | ✅ (35 tests) | ✅ (prior round) | ⬜ | ⬜ | ✅ (prior round) | |
| Memory | ✅ (test_vector.py, 3 tests) | ⬜ | ⬜ | ⬜ | ⬜ | Thin — needs more |
| Reminders | ✅ (21 tests) | ✅ (prior round, full CRUD) | ⬜ | ⬜ | ✅ (prior round) | |
| Shopping/Orders/Cart | ✅ (69+ tests) | ✅ (extensive, prior round) | 🔄 | ⬜ | ✅ (prior round) | Strongest-covered area |
| Voice | ✅ (4 tests, thin) | ✅ (prior round, endpoints) | ⬜ (no real mic) | ⬜ | ✅ (prior round) | |
| Models/routing | ⬜ | ✅ (prior round, switch endpoint) | ⬜ | ⬜ | ✅ (prior round) | |
| Frontend | — | — | — | — | — | No automated tests exist |
| API (general) | ⬜ | 🔄 | ⬜ | ⬜ | 🔄 | |

---

## Session log

**2026-08-31, session start:** Phase 0 discovery complete. Architecture map
built, 4 findings logged (F-001..F-004, none are bugs requiring a code fix
— documented characteristics). Beginning §4/43 shopping truthfulness live
test next.
