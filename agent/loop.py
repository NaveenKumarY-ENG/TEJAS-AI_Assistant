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
from memory import structured, vector
from tools import execute_tool, get_tool_schemas

logger = logging.getLogger("assistant.loop")

# Answers grounded in these tools are only true at the moment they were
# fetched (the date, the weather, free disk space...). Semantic memory has no
# concept of expiry, so persisting them lets a stale answer resurface later
# as "relevant past context" and get echoed back as if still current — this
# is exactly how a wrong date, once stated, kept reappearing in every future
# session even after the underlying date logic was fixed. Turns that used
# one of these tools are excluded from vector.remember() entirely.
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

    def _messages_for_llm(self, user_input: str) -> list[dict]:
        """
        Build the payload sent to the model: history, with the live date/
        time and any recalled memory context attached to the latest user
        turn only.

        The returned list is a throwaway copy - self.history is untouched,
        so the enriched text is never persisted or shown to the user. The
        live clock lives here rather than in the system prompt (see
        config.static_system_prompt) specifically so the system prompt
        stays byte-identical across calls — Ollama caches the KV state for
        a matching prompt prefix, and reusing that cache (instead of
        reprocessing the tool schema from scratch every time) cuts real
        response time roughly 3x, confirmed by direct testing.
        """
        parts = [config.current_time_context()]

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
        return messages

    # ------------------------------------------------------------------
    # Chat entry points
    # ------------------------------------------------------------------

    def chat(self, user_input: str) -> str:
        """Non-streaming turn: user message in, final assistant text out."""
        self._record("user", user_input)
        self._trim_history()

        messages = self._messages_for_llm(user_input)
        final_text, used_volatile_tool = self._run_tool_loop(messages)

        if not used_volatile_tool and not _is_volatile_query(user_input):
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
        messages = self._messages_for_llm(user_input)
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
        if not used_volatile_tool and not _is_volatile_query(user_input):
            vector.remember(f"User: {user_input}\nAssistant: {final}")
        return final

    # ------------------------------------------------------------------
    # Non-streaming tool loop
    # ------------------------------------------------------------------

    def _run_tool_loop(self, messages: list[dict]) -> tuple[str, bool]:
        """Returns (final_text, used_volatile_tool) — see VOLATILE_TOOLS."""
        used_volatile_tool = False

        for iteration in range(config.max_tool_iterations):
            response = call_llm(messages, self.tool_schemas)
            message = response["message"]

            tool_calls = message.get("tool_calls") or []
            content = message.get("content", "")

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