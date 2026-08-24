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
_KNOWLEDGE_LISTING_RE = re.compile(r"knowledge\s*(base|vase)", re.IGNORECASE)


def _is_knowledge_listing_query(text: str) -> bool:
    return bool(_KNOWLEDGE_LISTING_RE.search(text))


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

        Knowledge-base search runs unconditionally every turn, exactly like
        vector.recall() below — not gated on any regex heuristic. An
        earlier version of this only searched when the message looked like
        it named a specific file, and separately just *asked* the model to
        call search_knowledge as a tool when it did — both were confirmed
        live, repeatedly, to be unreliable on a 7B local model: it would
        still sometimes skip the tool call and fabricate a "couldn't find
        anything" answer. Never leaving that decision to the model at all
        (same reasoning that already applies to memory recall — the model
        doesn't "decide" to remember things either) is the actual fix.
        knowledge.search()'s own relevance threshold means an unrelated turn
        just gets nothing injected, same as vector.recall() returning [].
        """
        parts = [config.current_time_context()]

        listing = knowledge.document_listing()
        if listing:
            parts.append("Documents currently in the knowledge base (for reference — use search_knowledge or the content below for details on any of them):\n" + listing)

        kb_results = knowledge.search(user_input, n_results=3)
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
        final_text, used_volatile_tool = self._run_tool_loop(messages, structured_tables)

        # See VOLATILE_TOOLS's comment: a knowledge-base-grounded answer is
        # a snapshot of the documents as they exist right now, and must be
        # re-fetched fresh next time, not replayed from memory once they
        # change (a re-extraction, a retag, a deletion).
        if (
            not used_volatile_tool
            and not kb_results
            and not _is_volatile_query(user_input)
            and not _is_knowledge_listing_query(user_input)
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
                if fn["name"] in VOLATILE_TOOLS:
                    used_volatile_tool = True
                if on_tool:
                    on_tool(fn["name"])
                result = execute_tool(fn["name"], fn.get("arguments", {}))
                logger.debug("Tool %s -> %s", fn["name"], str(result)[:200])
                if on_tool_result:
                    on_tool_result(fn["name"], result)
                self._record("tool", result, name=fn["name"])

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
        ):
            vector.remember(f"User: {user_input}\nAssistant: {final}")
        return final

    # ------------------------------------------------------------------
    # Non-streaming tool loop
    # ------------------------------------------------------------------

    def _run_tool_loop(self, messages: list[dict], structured_tables: list[str] | None = None) -> tuple[str, bool]:
        """Returns (final_text, used_volatile_tool) — see VOLATILE_TOOLS."""
        used_volatile_tool = False

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
                if fn["name"] in VOLATILE_TOOLS:
                    used_volatile_tool = True
                result = execute_tool(fn["name"], fn.get("arguments", {}))
                logger.debug("Tool %s -> %s", fn["name"], str(result)[:200])
                self._record("tool", result, name=fn["name"])

            messages = list(self.history)

        return (
            "I hit my tool-call limit for this turn. The task may be more "
            "complex than expected - want me to keep going?"
        ), used_volatile_tool