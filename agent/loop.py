"""
The core agent loop, adapted for Ollama's local tool-calling format:

    user input -> LLM call (with tool schemas)
        -> if the model requests tool(s): execute them, feed results back, repeat
        -> if the model returns plain text: done, return it

Handles semantic memory injection, history trimming, streaming output,
and SQLite-backed session persistence.

Note on memory injection: recalled context is attached to the outgoing LLM
payload only. It is never recorded to history or persisted, so it never
appears in the UI or leaks into future turns.
"""
import logging
import re

from agent.llm_client import call_llm, call_llm_streaming
from config import config
from memory import knowledge, structured, vector
from tools import execute_tool, get_tool_schemas
from tools.browse_tool import google_search_url_from_text

logger = logging.getLogger("assistant.loop")

# Answers grounded in these tools are only true at the moment they were
# fetched (the date, the weather, free disk space...). Semantic memory has no
# concept of expiry, so persisting them lets a stale answer resurface later
# as "relevant past context" and get echoed back as if still current — this
# is exactly how a wrong date, once stated, kept reappearing in every future
# session even after the underlying date logic was fixed. Turns that used
# one of these tools are excluded from vector.remember() entirely.
#
# Knowledge-base-grounded turns get the same treatment (see the
# used_knowledge_base checks in chat()/chat_streaming() below), for the
# identical reason discovered live: a document's extracted data can change
# (re-extraction with an improved prompt, a retag, a re-upload) or the
# document can be deleted outright, and a memorized old answer doesn't know
# that happened — confirmed hitting this exact bug: the model's own
# once-garbled answer about an ID card got memorized, and kept getting
# recalled and repeated verbatim even after the underlying extraction was
# fixed to be accurate, because the "fix" only touched the source of truth,
# not the stale copy already sitting in semantic memory.
VOLATILE_TOOLS = {"get_weather", "get_current_datetime", "get_system_info"}

# The current date/time is now grounded directly in the outgoing prompt (see
# config.current_time_context, injected per-turn in _messages_for_llm below),
# so the model often answers date/weather/system-status questions WITHOUT
# calling a tool at all — VOLATILE_TOOLS
# alone would miss those turns. This catches them by the question itself.
# False positives just mean "skip remembering," a safe failure mode; false
# negatives are what caused the original bug, so this errs broad.
_VOLATILE_QUERY_RE = re.compile(
    r"\b(today|current(ly)?|right now|what time|what day|what date|"
    r"day is it|date is it|weather|forecast|temperature outside|"
    r"disk space|free space)\b",
    re.IGNORECASE,
)


def _is_volatile_query(text: str) -> bool:
    return bool(_VOLATILE_QUERY_RE.search(text))


# A "what's in my knowledge base" / "what do you have" question is answered
# from knowledge.document_listing() (see _messages_for_llm), not from
# knowledge.search() — nothing in a document's own chunk text says "here is
# the complete list of documents," so kb_results comes back empty for this
# kind of question and the kb_results-based memorization guard below misses
# it entirely. Confirmed live: exactly this happened — an old listing answer
# (from when the knowledge base held different documents) got memorized,
# and kept being recalled and blended into new listing answers even after
# the documents changed, producing inconsistent replies (sometimes correct,
# sometimes naming documents that had long since been deleted). The listing
# is exactly as volatile as VOLATILE_TOOLS/_is_volatile_query above — same
# fix, same reasoning. "vase" covers a real observed typo/mishearing of
# "base"; matching broadly is safe here since a false positive only means
# "skip remembering."
#
# "know\w*" (not the literal word "knowledge") confirmed live as a real,
# actually-triggered bug: a genuine typo ("what does knowleddge base has")
# didn't match the old knowledge-literal pattern, so that answer (itself
# naming a document that no longer exists) got memorized anyway and kept
# being recalled as fact for every later "what's in my knowledge base"
# question. "know" plus any word characters covers knowledge/knowldge/
# knowlege/knowleddge and similar without needing to enumerate every typo.
_KNOWLEDGE_LISTING_RE = re.compile(r"know\w*\s*(base|vase)", re.IGNORECASE)


def _is_knowledge_listing_query(text: str) -> bool:
    return bool(_KNOWLEDGE_LISTING_RE.search(text))


# Same reasoning as _is_knowledge_listing_query above, confirmed live the
# identical way: a "list my reminders" (or "delete"/"reschedule my X
# reminder") exchange got memorized, and a later "list my reminders" turn
# recalled that OLD exchange as "relevant past context" and used it as
# supporting grounding for repeating the exact same wrong answer — in one
# live case, a completely fabricated "Buy milk" reminder that never existed
# kept resurfacing in later turns even though it was never in the real
# database. Reminders are exactly as volatile as VOLATILE_TOOLS/knowledge-
# base listings: the underlying data can change (add/update/complete/
# delete) at any moment, so a memorized answer about them has no way to
# know it's gone stale. Matching broadly ("reminder" alone, no attempt to
# distinguish add/list/update/delete) is safe — a false positive only means
# "skip remembering," never a wrong answer.
_REMINDER_QUERY_RE = re.compile(r"remind(er|ers|ing)?\b", re.IGNORECASE)


def _is_reminder_query(text: str) -> bool:
    return bool(_REMINDER_QUERY_RE.search(text))


# QA audit finding B-005: a clear "add X to cart" request could silently go
# unfulfilled — the model called shop_amazon (search) alone, then narrated
# unsolicited accessory recommendations instead of ever calling
# order_amazon, with no cart action taken and no error surfaced. Confirmed
# reproducible (not a sampling fluke) against qwen2.5:7b; a static system-
# prompt rule alone did NOT fix it. Same root cause and same fix shape as
# the reminder-listing bug above ("a 7B model sometimes skips calling the
# right tool and improvises instead") — reinforcing the instruction right
# next to the user's own message, every matching turn, rather than relying
# on a rule buried in the (cached, rarely re-attended-to) static system
# prompt.
#
# Deliberately scoped to "add/put ... to/in (my/the) cart" specifically —
# not "order"/"buy" alone, which are too ambiguous (e.g. "order me a
# pizza," "should I buy this stock") and risk the opposite failure: forcing
# a real cart action the user never actually asked for. "add to cart" has
# no such ambiguity — there's no ordinary meaning of that exact phrase
# other than wanting the item added to a shopping cart.
_CART_REQUEST_RE = re.compile(
    r"\b(?:add|put)\b.{0,200}?\b(?:to|in)\s+(?:my\s+|the\s+)?(?:amazon(?:\.in)?\s+)?cart\b", re.IGNORECASE
)


def _is_cart_request_query(text: str) -> bool:
    return bool(_CART_REQUEST_RE.search(text))


# Confirmed live: "Search the web for best AIML courses" pulled in the
# user's own uploaded "AIML.pdf" as ambient knowledge-base context (see
# _messages_for_llm below) purely on a keyword/topic coincidence — the
# filename/content happened to overlap "AIML" with the query — even though
# the user explicitly said "the web," not "my documents." A 7B local model
# then got visibly confused between the two sources, ending up trying to
# read the local document as if it were a web page instead of just
# answering from web_search. knowledge.search()'s relevance threshold has
# no way to know the user's INTENT was "the open internet, not my own
# files" — it can only judge topical/keyword similarity, so a document
# that's genuinely about a similar topic (or just similarly named) will
# keep passing that threshold regardless. When the user's own words make
# that intent this explicit, skip the ambient knowledge-base injection for
# the turn entirely rather than hoping the model sorts out two contradictory
# sources on its own — same "an explicit signal beats hoping the model
# ignores irrelevant ambient context" reasoning as every regex above.
_EXPLICIT_WEB_QUERY_RE = re.compile(
    r"\b(?:search|look\s*up|find|research)\b.{0,40}\b(?:the\s+)?(?:web|internet|online|google)\b"
    r"|\bon\s+(?:the\s+)?internet\b",
    re.IGNORECASE,
)


def _is_explicit_web_query(text: str) -> bool:
    return bool(_EXPLICIT_WEB_QUERY_RE.search(text))


def _tool_call_key(name: str, arguments: dict) -> tuple:
    """A hashable fingerprint for "this exact tool call, with these exact
    arguments" — every tool in this codebase takes a flat dict of
    strings/numbers/bools (see tools/base.py's Tool.input_schema
    convention), so sorting its items is enough for a stable, order-
    independent key."""
    return (name, tuple(sorted((arguments or {}).items())))


_SINGLE_SHOT_TOOL_CALL_LIMIT: dict[str, int] = {
    # Confirmed live as a real, still-present gap even with the exact-
    # (name, args) dedup below in place: a local model can dodge that
    # specific check by inventing a different, sometimes nonsensical
    # argument on each retry ("open g-shock" -> then, unprompted, "open
    # example.com") instead of ever stopping to reply — so counting exact
    # repeats alone wasn't enough, and even allowing exactly 2 real calls
    # (to leave room for a genuine "open X and Y" dual-site ask) still let
    # a SECOND, wrong, dodge-argument call through in practice — confirmed
    # live, the same session that opened the right page correctly on
    # attempt 1 then opened https://www.example.com on attempt 2 instead
    # of just replying. Explicit product requirement: "open only once and
    # valid once." One real call is the limit — a real dual-site request
    # is rare enough that ending the turn cleanly after the first (correct)
    # site, rather than risking a second wrong one, is the better default.
    # Deliberately scoped to open_website specifically, not every tool — a
    # tool like web_search genuinely can need several different queries in
    # one turn (comparing options), so a blanket cap on every tool would
    # be wrong there.
    "open_website": 1,
}


def _repeated_tool_call_notice(name: str) -> str:
    """Confirmed live as a real, severe, reliably-reproduced failure mode
    — NOT something a system-prompt rule alone fixed, so (matching this
    codebase's established pattern: don't leave an unreliable judgment
    call to the model — see knowledge-base search/reminder-listing/
    structured-table handling above, all fixed the same way) this is
    enforced deterministically in code instead. Asked to "open wikipedia,"
    a 7B local model called open_website, got back a real, unambiguous
    success ("Opened https://www.wikipedia.org for you in a browser
    window"), and then called the exact same tool with the exact same
    argument 7 more times in a row — burning the entire per-turn tool
    budget on repeats of a call that had already succeeded the first
    time, never once replying to the user. Skipping the actual
    re-execution (real browser navigations/API calls are not free, and
    repeating one doesn't produce new information anyway) and substituting
    this instead gives the model a maximally explicit, impossible-to-miss
    signal to stop and answer, right where it's looking (the tool result
    it just "received"), rather than hoping a rule stated once, far above
    in the system prompt, gets re-attended to on every iteration."""
    return (
        f"(You already called {name} with these exact arguments earlier this turn and got a real "
        "result — calling it again with the same arguments will not produce a different one. Stop "
        "calling tools and reply to the user now, using the result you already have.)"
    )


def _apply_open_website_overrides(name: str, args: dict, user_input: str) -> dict:
    """Confirmed live as a real, still-reachable gap even after resolve_url
    (tools/browse_tool.py) learned to turn a "<topic> in google.com" PHRASE
    into a real Google search URL, and the system prompt was told to pass
    the phrase through instead of guessing: a local model can still ignore
    both and fabricate a specific, plausible-looking but entirely
    nonexistent article URL (e.g.
    "https://www.example.com/live-cricket-score-afghan-vs-india") as the
    site argument — which "successfully" navigates to a 404 instead of
    anything real. The one thing that's always trustworthy here is the
    user's OWN original wording for this turn, independent of whatever the
    model decided to pass, so when it explicitly said "google"/"google.com"
    this turn, that always wins over the model's argument — matching this
    codebase's established pattern of enforcing this kind of thing in code
    rather than trusting the model to get it right."""
    if name != "open_website":
        return args
    google_url = google_search_url_from_text(user_input)
    if not google_url:
        return args
    return {**args, "site": google_url}


class Agent:
    def __init__(self, session_id: int | None = None, resume: bool = False):
        """
        Three ways to start:
          - session_id given: reopen that exact session, loading its history.
          - resume=True (no session_id): continue the most recent session,
            creating one if none exist yet.
          - neither: start a brand new, empty session.
        """
        self.tool_schemas = get_tool_schemas()

        if session_id is not None:
            self.session_id = session_id
            self.history = structured.load_messages(self.session_id, limit=config.max_history_messages)
            logger.info("Opened session %d with %d messages", self.session_id, len(self.history))
        elif resume:
            existing = structured.get_latest_session()
            self.session_id = existing if existing is not None else structured.create_session()
            self.history = structured.load_messages(
                self.session_id, limit=config.max_history_messages
            )
            logger.info("Resumed session %d with %d messages", self.session_id, len(self.history))
        else:
            self.session_id = structured.create_session()
            self.history: list[dict] = []
            logger.info("Started new session %d", self.session_id)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _record(self, role: str, content: str, name: str | None = None) -> None:
        """
        Add a message to in-memory history AND persist it.
        Always stores the RAW text - never the memory-enriched version.
        """
        msg = {"role": role, "content": content}
        if name:
            msg["name"] = name
        self.history.append(msg)
        structured.save_message(self.session_id, role, content, name)

    def _trim_history(self) -> None:
        """
        Keep history bounded so each turn doesn't resend an ever-growing transcript.
        Trims from the oldest end, but never leaves a dangling tool result whose
        originating assistant message was dropped.
        """
        if len(self.history) <= config.max_history_messages:
            return

        trimmed = self.history[-config.max_history_messages:]

        # Drop leading orphaned tool messages (their assistant turn is gone).
        while trimmed and trimmed[0].get("role") == "tool":
            trimmed.pop(0)

        self.history = trimmed

    def _messages_for_llm(self, user_input: str) -> tuple[list[dict], list[dict]]:
        """
        Build the payload sent to the model: history, with the live date/
        time, any recalled memory context, and any relevant knowledge-base
        content attached to the latest user turn only.

        The returned messages list is a throwaway copy - self.history is
        untouched, so the enriched text is never persisted or shown to the
        user. The live clock lives here rather than in the system prompt
        (see config.static_system_prompt) specifically so the system prompt
        stays byte-identical across calls — Ollama caches the KV state for
        a matching prompt prefix, and reusing that cache (instead of
        reprocessing the tool schema from scratch every time) cuts real
        response time roughly 3x, confirmed by direct testing.

        Also returns the raw knowledge-base results (possibly []), so
        chat_streaming can fire on_tool/on_tool_result for the citation UI
        even though nothing "called" search_knowledge this turn.

        Knowledge-base search runs every turn by default, exactly like
        vector.recall() below — not left to the model to decide whether to
        call search_knowledge itself. An earlier version of this only
        searched when the message looked like it named a specific file, and
        separately just *asked* the model to call search_knowledge as a
        tool when it did — both were confirmed live, repeatedly, to be
        unreliable on a 7B local model: it would still sometimes skip the
        tool call and fabricate a "couldn't find anything" answer. Never
        leaving that decision to the model at all (same reasoning that
        already applies to memory recall — the model doesn't "decide" to
        remember things either) is the actual fix. knowledge.search()'s own
        relevance threshold means an unrelated turn just gets nothing
        injected, same as vector.recall() returning [].

        The one deliberate exception: _is_explicit_web_query below skips
        this injection entirely when the user's own words make it clear
        they want the open internet, not their own files ("search the
        web...") — confirmed live that an unrelated document can still pass
        knowledge.search()'s relevance threshold on a topical/keyword
        coincidence (a document literally named "AIML.pdf" for a "best AIML
        courses" web query), and a 7B model gets visibly confused between
        two contradictory sources it was never asked to reconcile.
        """
        parts = [config.current_time_context()]

        reminder_listing = structured.reminder_listing()
        if reminder_listing:
            parts.append(
                "Your current reminders (for reference — use this to answer 'list my "
                "reminders' directly, and to find the correct reminder_id when asked to "
                "update/complete/delete one by description, rather than guessing):\n"
                + reminder_listing
                + "\n\nThis listing is read-only reference material. If the user asks to add, "
                "update, complete, or delete a reminder, you must still call manage_reminders "
                "for it right now — this listing reflects the state BEFORE any action you take "
                "this turn, so it will look unchanged until you actually call the tool."
            )

        if _is_cart_request_query(user_input):
            # See _CART_REQUEST_RE's comment (B-005) — reinforced right next
            # to the user's own message, every matching turn, the same fix
            # shape already proven for the analogous reminder-tool-skipping
            # bug above.
            parts.append(
                "This message looks like a request to add a specific product to the user's "
                "Amazon cart. You MUST call order_amazon right now as your very next step, with "
                "product_name set to the product they described (or product_url if they gave a "
                "link) — do NOT call shop_amazon alone and then describe, recommend, or list "
                "results instead; that is not what was asked, even if the search turns up "
                "accessories or similar items alongside the actual product. If order_amazon "
                "reports it couldn't confirm an exact match, relay that message to the user "
                "plainly rather than substituting your own recommendation for it."
            )

        explicit_web_query = _is_explicit_web_query(user_input)
        if explicit_web_query:
            # See _EXPLICIT_WEB_QUERY_RE's comment — reinforced right next
            # to the user's own message, same pattern as the cart-request
            # instruction above, for the same reason: a 7B model following
            # a rule buried in the (cached, rarely re-attended-to) static
            # system prompt alone wasn't reliable enough on its own.
            parts.append(
                "This message explicitly asks for a live web search, not the user's own "
                "uploaded documents. Call web_search now and answer from those results. Do NOT "
                "call search_knowledge or treat any knowledge-base document as the answer here, "
                "even if one happens to share a similar-looking name or topic with the query."
            )

        listing = knowledge.document_listing() if not explicit_web_query else None
        if listing:
            parts.append("Documents currently in the knowledge base (for reference — use search_knowledge or the content below for details on any of them):\n" + listing)

        kb_results = knowledge.search(user_input, n_results=3) if not explicit_web_query else []
        if kb_results:
            parts.append(
                "Relevant content from the knowledge base:\n" + knowledge.format_search_results(kb_results)
            )
            logger.debug("Injected %d knowledge-base result(s)", len(kb_results))
            if any(knowledge.is_structured_table(r["text"]) for r in kb_results):
                # The exact table(s) get appended verbatim after this reply
                # (see chat_streaming) — the model only needs to write a
                # short intro, never retype the data itself. See
                # knowledge.is_structured_table's docstring for why this
                # isn't left to a "present it as-is" instruction alone.
                parts.append(
                    "One or more of the knowledge base results above is a document's exact extracted "
                    "field table. The real table is shown automatically right after your reply — you do "
                    "not need to, and must NOT, write out any field name or value yourself, in any form "
                    "(no bullet points, no bold labels, no partial list). Respond with ONLY one short "
                    "sentence like 'Here are the details:' and then stop generating immediately. Writing "
                    "even one field yourself means it will appear twice — once wrong from you, once "
                    "correct from the table."
                )

        recalled = vector.recall(user_input, n_results=3)
        if recalled:
            parts.append(
                "Relevant context from past conversations:\n" + "\n".join(f"- {r}" for r in recalled)
            )
            logger.debug("Injected %d recalled memories", len(recalled))

        context = "\n\n".join(parts)
        messages = list(self.history[:-1])
        messages.append(
            {"role": "user", "content": f"{context}\n\nUser's message: {user_input}"}
        )
        return messages, kb_results

    # ------------------------------------------------------------------
    # Chat entry points
    # ------------------------------------------------------------------

    def chat(self, user_input: str) -> str:
        """Non-streaming turn: user message in, final assistant text out."""
        self._record("user", user_input)
        self._trim_history()

        messages, kb_results = self._messages_for_llm(user_input)
        structured_tables = [r["text"] for r in kb_results if knowledge.is_structured_table(r["text"])]
        final_text, used_volatile_tool = self._run_tool_loop(messages, structured_tables, user_input)

        # See VOLATILE_TOOLS's comment: a knowledge-base-grounded answer is
        # a snapshot of the documents as they exist right now, and must be
        # re-fetched fresh next time, not replayed from memory once they
        # change (a re-extraction, a retag, a deletion).
        if (
            not used_volatile_tool
            and not kb_results
            and not _is_volatile_query(user_input)
            and not _is_knowledge_listing_query(user_input)
            and not _is_reminder_query(user_input)
        ):
            vector.remember(f"User: {user_input}\nAssistant: {final_text}")
        return final_text

    def chat_streaming(self, user_input: str, on_chunk, on_tool=None, on_tool_result=None) -> str:
        """
        Streaming turn: calls on_chunk(text) as each piece arrives, on_tool(name)
        when a tool is about to run, and on_tool_result(name, result) once it
        returns. Tool-calling iterations produce no user-visible text; only
        the final answer streams. on_tool_result is generic (fires for any
        tool) — the caller (server.py) decides which tools' results are
        actually worth sending to the frontend (e.g. search_knowledge, for
        citations) rather than that being baked in here.
        """
        self._record("user", user_input)
        self._trim_history()

        # First pass uses the memory-enriched payload; later iterations use
        # clean history (the model already has the context from pass one).
        messages, kb_results = self._messages_for_llm(user_input)
        # Exact document field tables among this turn's results — appended
        # verbatim after the model's own final text below, rather than
        # trusted to survive the model retyping them. See
        # knowledge.is_structured_table's docstring: confirmed live that a
        # 7B local model will still "helpfully" alter a value (inventing a
        # plausible-looking date for an illegible OCR field, for instance)
        # even with an explicit "present this as-is" instruction — the same
        # instruction-following unreliability already seen with tool-calling,
        # fixed the same way: don't leave it to the model at all.
        structured_tables = [r["text"] for r in kb_results if knowledge.is_structured_table(r["text"])]
        if kb_results:
            # Fires the same UI events a real search_knowledge tool call
            # would (tool pill + citation caption) even though nothing
            # "called" it this turn — see _messages_for_llm's docstring for
            # why this is proactive rather than tool-call-dependent.
            if on_tool:
                on_tool("search_knowledge")
            if on_tool_result:
                on_tool_result("search_knowledge", knowledge.format_search_results(kb_results))
        full_text = ""
        used_volatile_tool = False
        # See _repeated_tool_call_notice's docstring — tracks (name, args)
        # pairs already executed THIS turn so an exact repeat can be
        # caught and short-circuited instead of silently re-run.
        seen_tool_calls: set[tuple] = set()
        # See _SINGLE_SHOT_TOOL_CALL_LIMIT's docstring — tracks how many
        # times each tool name has genuinely executed this turn (not
        # counting skipped exact-repeats), and the last real result each
        # one produced, so a tool that hits its limit can end the turn
        # immediately with a real answer instead of looping further.
        tool_name_counts: dict[str, int] = {}
        last_result_by_name: dict[str, str] = {}
        force_ended = False

        for iteration in range(config.max_tool_iterations):
            stream = call_llm_streaming(messages, self.tool_schemas)

            chunk_text = ""
            tool_calls = []

            for chunk in stream:
                message = chunk.get("message", {})

                piece = message.get("content") or ""
                if piece:
                    chunk_text += piece
                    on_chunk(piece)

                if message.get("tool_calls"):
                    tool_calls.extend(message["tool_calls"])

            if not tool_calls and structured_tables:
                appendix = "\n\n" + "\n\n".join(structured_tables)
                on_chunk(appendix)
                chunk_text += appendix

            self._record("assistant", chunk_text)
            full_text += chunk_text

            if not tool_calls:
                break

            logger.info("Iteration %d: executing %d tool call(s)", iteration, len(tool_calls))
            for call in tool_calls:
                fn = call["function"]
                args = fn.get("arguments", {}) or {}
                args = _apply_open_website_overrides(fn["name"], args, user_input)

                limit = _SINGLE_SHOT_TOOL_CALL_LIMIT.get(fn["name"])
                if limit is not None and tool_name_counts.get(fn["name"], 0) >= limit:
                    # Don't call it again, don't just notify-and-hope —
                    # end the turn right now with the last real result
                    # this tool already produced. See
                    # _SINGLE_SHOT_TOOL_CALL_LIMIT's docstring. Deliberately
                    # skips on_tool here too: confirmed live that firing
                    # on_tool for every call the MODEL asked for (rather
                    # than only the ones actually executed) is exactly what
                    # produced the original reported bug — a stack of
                    # duplicate "open website" pills in the UI even after
                    # the backend itself only ever ran the tool once.
                    final_result = last_result_by_name.get(fn["name"]) or _repeated_tool_call_notice(fn["name"])
                    logger.warning(
                        "%s hit its %d-call limit for this turn — ending the turn with its last "
                        "real result instead of calling it again.",
                        fn["name"],
                        limit,
                    )
                    if on_tool_result:
                        on_tool_result(fn["name"], final_result)
                    on_chunk(final_result)
                    full_text = final_result
                    self._record("assistant", final_result)
                    force_ended = True
                    break

                call_key = _tool_call_key(fn["name"], args)
                if call_key in seen_tool_calls:
                    logger.warning("Skipping a repeated identical tool call: %s(%s)", fn["name"], args)
                    result = _repeated_tool_call_notice(fn["name"])
                else:
                    # on_tool fires only for a call that's actually about to
                    # run — not once per call the model merely asked for —
                    # so the UI's tool-call pills reflect real executions
                    # one-to-one, never a duplicate for a request that was
                    # capped or deduped away before it ever ran.
                    if on_tool:
                        on_tool(fn["name"])
                    seen_tool_calls.add(call_key)
                    tool_name_counts[fn["name"]] = tool_name_counts.get(fn["name"], 0) + 1
                    last_result_by_name[fn["name"]] = result = execute_tool(fn["name"], args)
                    if fn["name"] in VOLATILE_TOOLS:
                        used_volatile_tool = True
                    logger.debug("Tool %s(%s) -> %s", fn["name"], args, str(result)[:200])
                if on_tool_result:
                    on_tool_result(fn["name"], result)
                self._record("tool", result, name=fn["name"])

            if force_ended:
                break
            messages = list(self.history)
        else:
            # Loop exhausted without breaking - hit the iteration cap.
            msg = (
                "I hit my tool-call limit for this turn. The task may be more "
                "complex than expected - want me to keep going?"
            )
            on_chunk(msg)
            full_text += msg

        final = full_text.strip() or "(no response)"
        # See VOLATILE_TOOLS's comment above and chat()'s matching check.
        if (
            not used_volatile_tool
            and not kb_results
            and not _is_volatile_query(user_input)
            and not _is_knowledge_listing_query(user_input)
            and not _is_reminder_query(user_input)
        ):
            vector.remember(f"User: {user_input}\nAssistant: {final}")
        return final

    # ------------------------------------------------------------------
    # Non-streaming tool loop
    # ------------------------------------------------------------------

    def _run_tool_loop(
        self, messages: list[dict], structured_tables: list[str] | None = None, user_input: str = ""
    ) -> tuple[str, bool]:
        """Returns (final_text, used_volatile_tool) — see VOLATILE_TOOLS."""
        used_volatile_tool = False
        # See _repeated_tool_call_notice's/_SINGLE_SHOT_TOOL_CALL_LIMIT's
        # docstrings — same fix as chat_streaming's identical loop.
        seen_tool_calls: set[tuple] = set()
        tool_name_counts: dict[str, int] = {}
        last_result_by_name: dict[str, str] = {}

        for iteration in range(config.max_tool_iterations):
            response = call_llm(messages, self.tool_schemas)
            message = response["message"]

            tool_calls = message.get("tool_calls") or []
            content = message.get("content", "")

            if not tool_calls and structured_tables:
                content = (content or "") + "\n\n" + "\n\n".join(structured_tables)

            self._record("assistant", content)

            if not tool_calls:
                return (content or "").strip() or "(no response)", used_volatile_tool

            logger.info("Iteration %d: executing %d tool call(s)", iteration, len(tool_calls))
            for call in tool_calls:
                fn = call["function"]
                args = fn.get("arguments", {}) or {}
                args = _apply_open_website_overrides(fn["name"], args, user_input)

                limit = _SINGLE_SHOT_TOOL_CALL_LIMIT.get(fn["name"])
                if limit is not None and tool_name_counts.get(fn["name"], 0) >= limit:
                    final_result = last_result_by_name.get(fn["name"]) or _repeated_tool_call_notice(fn["name"])
                    logger.warning(
                        "%s hit its %d-call limit for this turn — ending the turn with its last "
                        "real result instead of calling it again.",
                        fn["name"],
                        limit,
                    )
                    self._record("assistant", final_result)
                    return final_result.strip(), used_volatile_tool

                call_key = _tool_call_key(fn["name"], args)
                if call_key in seen_tool_calls:
                    logger.warning("Skipping a repeated identical tool call: %s(%s)", fn["name"], args)
                    result = _repeated_tool_call_notice(fn["name"])
                else:
                    seen_tool_calls.add(call_key)
                    tool_name_counts[fn["name"]] = tool_name_counts.get(fn["name"], 0) + 1
                    last_result_by_name[fn["name"]] = result = execute_tool(fn["name"], args)
                    if fn["name"] in VOLATILE_TOOLS:
                        used_volatile_tool = True
                    logger.debug("Tool %s(%s) -> %s", fn["name"], args, str(result)[:200])
                self._record("tool", result, name=fn["name"])

            messages = list(self.history)

        return (
            "I hit my tool-call limit for this turn. The task may be more "
            "complex than expected - want me to keep going?"
        ), used_volatile_tool