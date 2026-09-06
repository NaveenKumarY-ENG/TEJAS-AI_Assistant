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
| B-006 | **P0** | `tools/code_exec.py` `CodeExecutionTool.run` | `execute_python` (the calculator/code tool) crashed the **entire server** mid-response on every single call, in the exact dev setup the README recommends (`uvicorn server:app --reload`). Root cause: the tool's temp `.py` script was written into `config.sandbox_dir` (`data/sandbox/`), which sits inside the project root `--reload`'s file watcher monitors — every call's temp-file write was picked up as a real code change, triggering a full server restart that closed the in-flight WebSocket (`1012 service restart`) and killed the response. Since the process was torn down before the tool's own `finally` cleanup could run, the temp script was also leaked every time — a live sweep found 34 orphaned `tmp*.py` files in the sandbox from this alone. Reproduced cleanly and consistently via direct WS testing; would affect any real user running the app as documented. | **FIXED** — temp script now written to the system temp directory (`tempfile.gettempdir()`, the `NamedTemporaryFile` default) instead of the sandbox; the executed code's own `cwd` is unchanged (still the real sandbox). 16 new tests (this tool had zero coverage before), including a direct regression test asserting no temp script ever lands in the watched sandbox directory. **Live verified**: dozens of calculation requests across every non-cloud-paid model afterward, zero server restarts. |
| B-007 | P2 | System prompt / tool-routing (local model, `llama3.1:latest`) | Called the correct tool (`execute_python`, `get_weather`) successfully, but its final reply narrated the act of calling the tool ("I called the execute_python tool to calculate the result.") instead of stating the actual computed answer, for 3 of 5 test prompts. The user never sees the number/fact they asked for. | **Partially fixed** — added an explicit system-prompt rule requiring the actual result to be stated, not just described. Fixed the math case reliably on retest; the weather case still occasionally narrates instead of answering on this specific model. Documented as a known `llama3.1:latest`-specific limitation (not present on the default `qwen2.5:7b`) rather than chased further — see Session log. |
| B-008 | P2 | `tools/cart_tool.py` `_click_delete_and_confirm` | A real cart-item removal genuinely succeeded (confirmed by directly re-checking the live cart) but the tool reported "couldn't confirm it was actually removed" — a false-negative recurrence of the same flakiness class already fixed twice before in this function (see the function's own docstring history). Amazon's delete is an async server-side operation; even a freshly-reloaded cart page can momentarily still reflect the pre-delete state. | **FIXED** — added one retry with a 1.5s backoff before giving up, covering both an exception during the re-check and a clean re-check that still (momentarily) finds the item present. 1 new regression test simulating "still present on the first re-check, genuinely gone by the second." **Live verified**: repeated the exact add→remove round trip; reported success cleanly. |
| B-009 | P2 | `frontend/src/hooks/useSpeechRecognition.ts` | Exiting Voice Mode while a microphone-access error's 3.5s auto-clear-to-idle timer was still pending left the whole app's `coreState` **permanently stuck on "error"** (a persistent "⚠ ERROR" HUD badge on the Home screen) — the unmount cleanup effect cancelled the pending timer without ever running its actual job (clearing back to idle). Reproduced reliably: trigger a mic error in Voice Mode, exit within ~1s, the Home screen shows a stuck error indicator indefinitely (verified it does NOT self-clear even after 4.5s+, well past the intended 3.5s window) until some unrelated later state transition happens to overwrite it. | **FIXED** — the unmount cleanup now runs the clear-to-idle transition itself (guarded against stomping a legitimate later state) instead of just cancelling the timer silently. **Live verified**: reproduced the exact original repro steps post-fix — Home shows "● CORE ONLINE" immediately, no stuck error. |
| B-010 | P3 (design gap, not a bug) | Frontend responsive layout, `Sidebar.tsx` | At real mobile viewport widths (390px, e.g. a standard phone), the sidebar never collapses or hides — it renders at its full ~264px width, consuming most of the viewport and pushing all actual content (greeting, hologram, chat input) into an unusably narrow sliver. The existing icon-only "collapsed" sidebar state is a manual toggle only, never triggered automatically by viewport size, and there's no hamburger-menu/drawer alternative. | **Not fixed — flagged for a scoping decision.** This is a real UX gap, not a quick one-line fix: closing it properly means picking a mobile navigation pattern (auto-collapse below a breakpoint, a hamburger+drawer, or a bottom tab bar), which is a design decision worth the user's input rather than a unilateral change. |
| B-011 | P3 (informational) | Gemini free tier (`gemini:gemini-3.6-flash`) | `config.py`'s existing comment says `gemini-3.6-flash` was chosen specifically because the newer `3.7-flash`'s free tier was "capped at just 20 requests/day, too tight for real use," implying 3.6-flash wasn't similarly capped. Live testing this session hit a real `429 RESOURCE_EXHAUSTED` on `gemini-3.6-flash` itself with the identical `quotaValue: "20"` (20 requests/day) partway through a 5-prompt test batch. Google's free-tier limits for this model appear to have changed since that comment was written, or it was already stale. | **Needs a decision, not a code fix** — the comment/README should be corrected to reflect the current real limit (worth re-verifying which model, if any, currently offers a materially higher free-tier cap before recommending one over another). |

**Root cause analysis — B-001 (why it wasn't caught before):** `_best_match` was added in the previous session's "order by product name" feature and was live-tested only with product names that had NO color ambiguity in the actual top results (iQOO Z11, OnePlus Nord CE6) — the happy path where the top result already matched every attribute. The failure mode (best-available match ≠ requested variant) was never exercised because no prior test constructed a result set where the requested attribute was genuinely absent.

**Note on live Amazon testing (2026-09-06):** during a deliberately minimal
add→remove round trip (one cheap test item, on a real cart that already had
7 real unrelated items), a second, unrelated real item ("41 Foods Premium
Dry Fruits Combo," ₹500.00) was no longer present in two post-test checks.
Reviewed `_matches_by_words`'s scoping logic and found no mechanism that
would explain the removal call touching a second item — the word-matching
requires every word of the search phrase to appear in the target title, and
"colgate"/"toothpaste" doesn't match the dry-fruits item's title at all.
Could not fully rule in or out causation (an out-of-stock item can also be
silently dropped from a real Amazon cart independent of any tool call).
Flagged to the user directly; they confirmed it's fine and asked to
continue. Recorded here for anyone resuming this audit later — if this
pattern recurs, it's worth checking whether a *page-level* Amazon action
(not this tool's own targeted deletion) can affect more than the one row.

**Root cause analysis — B-002 (why it wasn't caught before):** Existing `test_llm_client.py` coverage only tested the pure data-transformation helpers (`_anthropic_messages`, `_anthropic_tools`, `_parse_anthropic_message`) — nothing exercised the actual SDK call. A prior test using a bare `MagicMock()` for the client would have silently accepted the broken `temperature=` kwarg and never caught this; only `create_autospec` against the real SDK class enforces the real signature. Prevention: the 2 new tests use exactly that mechanism.

---

## Regression matrix

| Feature | Unit | Integration | E2E | AI Eval | Negative | Status |
|---|---|---|---|---|---|---|
| Chat | ⬜ | ✅ (prior round) | ✅ (2026-09-06, 5-model battery) | ✅ (retrieval eval harness, 23 cases) | ✅ (prior round) | |
| RAG/Knowledge | ✅ (60+ tests) | ✅ (prior round) | ✅ | ✅ | ✅ (prior round) | |
| Memory | ✅ (test_vector.py, 3 tests) | ⬜ | ⬜ | ⬜ | ⬜ | Thin — needs more |
| Reminders | ✅ (21 tests) | ✅ (prior round, full CRUD) | ✅ (2026-09-06, real Calendar sync) | ⬜ | ✅ (prior round) | |
| Shopping/Orders/Cart | ✅ (69+ tests) | ✅ (extensive, prior round) | ✅ (2026-09-06) | ⬜ | ✅ (prior round) | Strongest-covered area; see live-testing note above |
| Calculator/code exec | ✅ (16 tests, was 0) | ✅ (2026-09-06) | ✅ (2026-09-06) | ✅ (5-model battery) | ✅ | Was completely broken under `--reload` (B-006) |
| Other tools (datetime/file_ops/system_info/web_search) | ✅ (37 tests, was 0) | — | — | — | ✅ (incl. sandbox-escape attempts) | Closed a real coverage gap |
| Voice | ✅ (4 tests, thin) | ✅ (prior round, endpoints) | ✅ (2026-09-06, error-state bug found+fixed) | ⬜ | ✅ (prior round) | |
| Models/routing | ⬜ | ✅ (prior round, switch endpoint) | ✅ (2026-09-06, 5 models compared) | ✅ | ✅ (prior round) | Gemini free tier caps at 20 req/day — see B-011 |
| Frontend | ✅ (32 tests, utils/store only) | — | ✅ (2026-09-06, Playwright screenshot sweep) | — | — | Mobile layout gap found (B-010) |
| API (general) | ⬜ | 🔄 | ⬜ | ⬜ | 🔄 | |

---

## Session log

**2026-08-31, session start:** Phase 0 discovery complete. Architecture map
built, 4 findings logged (F-001..F-004, none are bugs requiring a code fix
— documented characteristics). Beginning §4/43 shopping truthfulness live
test next.

**2026-09-06, full-project QA sweep** (prompted by: "go through the complete
project and make sure all are working properly... test all the cases...
perform all mathematical calculations possible and check all the current
models outputs... check orders and reminders... check the UI... check
recent sessions"). Backend test suite grew 312 → 360 (48 new tests: 16 for
`code_exec.py`, 6 for `datetime_tool.py`, 3 for `system_info.py`, 11 for
`file_ops.py` including sandbox-escape attempts, 9 for `web_search.py`, plus
regression tests for B-008/B-009). Found and fixed B-006 (P0, calculator
crashed the server under the documented dev command — the single most
impactful bug of this whole audit), B-007 (partial), B-008, B-009. Compared
math/tool-use quality across all 5 non-cloud-paid models (`qwen2.5:7b`,
`qwen3:8b`, `llama3.1:latest`, `gemma2:9b`, `gemini-3.6-flash`) with a
5-prompt battery — `qwen2.5:7b` (the default) was fastest and fully
correct; `qwen3:8b` correct but very slow (thinking-mode overhead, up to
66s/reply); `gemma2:9b` correctly reasons math directly (as expected, no
tool access) but can't fulfill a weather request, only acknowledge it can't;
`gemini-3.6-flash` hit its real 20-req/day free-tier cap mid-battery
(B-011). Verified reminders + real Google Calendar sync end-to-end
(add/list/update/complete) — flawless. Live-tested the real Amazon
cart add/remove flow — see the dedicated note above this log for a
real-cart discrepancy found, disclosed to the user, and accepted as-is.
Playwright screenshot sweep across desktop/tablet/mobile found B-009 (voice
error stuck state) and B-010 (sidebar unusable at real phone widths — flagged,
not fixed, needs a UX pattern decision). Cleaned up ~100 sessions this
session's own testing created from the user's real Recent Sessions list
before finishing. Full suite green (360/360) and frontend build/lint/test
(32/32) clean at every commit point.
