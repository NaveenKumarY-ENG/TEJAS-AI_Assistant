"""
LLM client — supports two backends behind one interface:

  - Ollama (default): local, free, private. Uses OpenAI-style tool calling
    natively, which is this app's internal message/tool-call shape.
  - Anthropic: hosted, costs money, much more reliable tool-calling.

Pick with LLM_PROVIDER=ollama|anthropic in .env. agent/loop.py never needs to
know which one is active — both call_llm() and call_llm_streaming() always
return/yield the same Ollama-shaped structures; the Anthropic path is a thin
adapter that translates in and out of that shape.
"""
import logging

import ollama
from anthropic import Anthropic

from config import config

logger = logging.getLogger("assistant.llm")

_anthropic_client: Anthropic | None = None


def _get_anthropic_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        if not config.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. "
                "Add it to .env, or switch LLM_PROVIDER back to 'ollama'."
            )
        _anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    return _anthropic_client


# ----------------------------------------------------------------------
# Ollama backend
# ----------------------------------------------------------------------

def _ollama_chat(messages: list[dict], tools: list[dict]):
    ollama_messages = [{"role": "system", "content": config.formatted_system_prompt()}] + messages
    logger.debug("Calling Ollama (%s) with %d messages", config.ollama_model, len(ollama_messages))
    return ollama.chat(
        model=config.ollama_model,
        messages=ollama_messages,
        tools=tools,
        options={"temperature": config.llm_temperature},
    )


def _ollama_chat_streaming(messages: list[dict], tools: list[dict]):
    ollama_messages = [{"role": "system", "content": config.formatted_system_prompt()}] + messages
    logger.debug("Streaming from Ollama (%s) with %d messages", config.ollama_model, len(ollama_messages))
    return ollama.chat(
        model=config.ollama_model,
        messages=ollama_messages,
        tools=tools,
        stream=True,
        options={"temperature": config.llm_temperature},
    )


# ----------------------------------------------------------------------
# Anthropic backend — translated to/from Ollama's shape
# ----------------------------------------------------------------------

def _anthropic_tools(tools: list[dict]) -> list[dict]:
    """This app's tool schemas are built once in OpenAI/Ollama shape
    (tools/__init__.py); translate to Anthropic's {name, description,
    input_schema} shape."""
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in tools
    ]


def _anthropic_messages(messages: list[dict]) -> list[dict]:
    """
    Translate this app's flat history (role: user/assistant/tool, tool
    entries carrying a `name`) into Anthropic's message format, where a tool
    call is a `tool_use` content block on an assistant message and its
    result is a matching `tool_result` block on the following user message.

    The app's persisted history only keeps a tool's name and result, not the
    arguments it was originally called with (Ollama's flat shape never
    required storing that). So the reconstructed tool_use blocks use a
    synthetic id and an empty `input` — Anthropic only validates that a
    tool_result's id matches a preceding tool_use in the same request, not
    that the input reflects reality, so this round-trips correctly even
    though the original arguments aren't echoed back to the model.
    """
    result: list[dict] = []
    i, n = 0, len(messages)

    while i < n:
        msg = messages[i]

        if msg["role"] != "assistant":
            result.append({"role": msg["role"], "content": msg["content"]})
            i += 1
            continue

        text = msg["content"]
        tool_msgs = []
        j = i + 1
        while j < n and messages[j]["role"] == "tool":
            tool_msgs.append(messages[j])
            j += 1

        if not tool_msgs:
            result.append({"role": "assistant", "content": text or "(no response)"})
            i += 1
            continue

        content_blocks = []
        if text:
            content_blocks.append({"type": "text", "text": text})

        tool_use_ids = [f"toolu_{i}_{k}" for k in range(len(tool_msgs))]
        for tid, t in zip(tool_use_ids, tool_msgs):
            content_blocks.append({"type": "tool_use", "id": tid, "name": t.get("name", "tool"), "input": {}})
        result.append({"role": "assistant", "content": content_blocks})

        result.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": t["content"]}
                    for tid, t in zip(tool_use_ids, tool_msgs)
                ],
            }
        )
        i = j

    return result


def _parse_anthropic_message(message) -> dict:
    """Normalize an Anthropic Message into this app's {message: {content,
    tool_calls}} shape."""
    text_parts = []
    tool_calls = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({"function": {"name": block.name, "arguments": block.input}})
    return {"message": {"content": "".join(text_parts), "tool_calls": tool_calls}}


def _anthropic_chat(messages: list[dict], tools: list[dict]):
    client = _get_anthropic_client()
    logger.debug("Calling Anthropic (%s) with %d messages", config.model, len(messages))
    response = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        temperature=config.llm_temperature,
        system=config.formatted_system_prompt(),
        messages=_anthropic_messages(messages),
        tools=_anthropic_tools(tools),
    )
    return _parse_anthropic_message(response)


def _anthropic_chat_streaming(messages: list[dict], tools: list[dict]):
    client = _get_anthropic_client()
    logger.debug("Streaming from Anthropic (%s) with %d messages", config.model, len(messages))

    def generator():
        with client.messages.stream(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.llm_temperature,
            system=config.formatted_system_prompt(),
            messages=_anthropic_messages(messages),
            tools=_anthropic_tools(tools),
        ) as stream:
            for text in stream.text_stream:
                yield {"message": {"content": text}}

            final = stream.get_final_message()
            tool_calls = [
                {"function": {"name": b.name, "arguments": b.input}}
                for b in final.content
                if b.type == "tool_use"
            ]
            if tool_calls:
                yield {"message": {"content": "", "tool_calls": tool_calls}}

    return generator()


# ----------------------------------------------------------------------
# Public interface — used by agent/loop.py, backend-agnostic
# ----------------------------------------------------------------------

def call_llm(messages: list[dict], tools: list[dict]):
    """Non-streaming call. Used when tools may be involved."""
    if config.llm_provider == "anthropic":
        return _anthropic_chat(messages, tools)
    return _ollama_chat(messages, tools)


def call_llm_streaming(messages: list[dict], tools: list[dict]):
    """
    Streaming call - yields chunks as they arrive.
    Note: tool calls arrive in the final chunk, so the caller must accumulate.
    """
    if config.llm_provider == "anthropic":
        return _anthropic_chat_streaming(messages, tools)
    return _ollama_chat_streaming(messages, tools)
