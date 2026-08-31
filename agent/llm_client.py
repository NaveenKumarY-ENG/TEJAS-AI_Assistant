"""
LLM client — supports three backends behind one interface:

  - Ollama (default): local, free, private. Uses OpenAI-style tool calling
    natively, which is this app's internal message/tool-call shape.
  - Anthropic: hosted, costs money, much more reliable tool-calling.
  - Gemini: hosted, free tier with no billing/card required (unlike
    Anthropic) — a good zero-cost bridge to a stronger-than-local model.

Pick with LLM_PROVIDER=ollama|anthropic|gemini in .env. agent/loop.py never
needs to know which one is active — call_llm() and call_llm_streaming()
always return/yield the same Ollama-shaped structures; the Anthropic and
Gemini paths are thin adapters that translate in and out of that shape.
"""
import logging

import ollama
from anthropic import Anthropic
from google import genai
from google.genai import types as gtypes

from config import config

logger = logging.getLogger("assistant.llm")

_anthropic_client: Anthropic | None = None
_gemini_client: genai.Client | None = None


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


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        if not config.gemini_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. "
                "Add it to .env (free key at aistudio.google.com/apikey), "
                "or switch LLM_PROVIDER back to 'ollama'."
            )
        # No retries happen by default (confirmed live — a 429 surfaced
        # immediately with no delay) unless retry_options is set explicitly.
        # Covers exactly the transient failures seen in practice: 503
        # ("model experiencing high demand" — Google's servers overloaded,
        # not this app's fault) and 429 (free-tier rate limit) both retry
        # automatically instead of failing the turn outright.
        _gemini_client = genai.Client(
            api_key=config.gemini_api_key,
            http_options=gtypes.HttpOptions(
                retry_options=gtypes.HttpRetryOptions(attempts=3, initial_delay=2.0, max_delay=15.0)
            ),
        )
    return _gemini_client


# ----------------------------------------------------------------------
# Ollama backend
# ----------------------------------------------------------------------

def _ollama_chat(messages: list[dict], tools: list[dict]):
    ollama_messages = [{"role": "system", "content": config.static_system_prompt()}] + messages
    kwargs = {
        "model": config.ollama_model,
        "messages": ollama_messages,
        "options": {"temperature": config.llm_temperature},
        "keep_alive": config.ollama_keep_alive,
    }
    # Some local models (e.g. gemma2) don't implement Ollama's tool-calling
    # template at all — passing `tools` makes Ollama reject the request
    # outright, so it's only included for models flagged as supporting it.
    if config.active_model_supports_tools:
        kwargs["tools"] = tools
    logger.debug("Calling Ollama (%s) with %d messages", config.ollama_model, len(ollama_messages))
    return ollama.chat(**kwargs)


def _ollama_chat_streaming(messages: list[dict], tools: list[dict]):
    ollama_messages = [{"role": "system", "content": config.static_system_prompt()}] + messages
    kwargs = {
        "model": config.ollama_model,
        "messages": ollama_messages,
        "stream": True,
        "options": {"temperature": config.llm_temperature},
        "keep_alive": config.ollama_keep_alive,
    }
    if config.active_model_supports_tools:
        kwargs["tools"] = tools
    logger.debug("Streaming from Ollama (%s) with %d messages", config.ollama_model, len(ollama_messages))
    return ollama.chat(**kwargs)


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
    # temperature via extra_body, not as a direct kwarg — confirmed live as
    # a real, total breakage: the installed anthropic SDK (1.0.0; this repo
    # only floors it at >=0.40.0, so a fresh install pulls in whatever
    # latest major version exists) dropped `temperature` from BOTH
    # Messages.create()'s AND Messages.stream()'s typed signatures
    # entirely — every single call via this provider raised
    # "unexpected keyword argument 'temperature'" before this fix, not a
    # rare edge case. The underlying REST API still accepts a top-level
    # `temperature` field (per Anthropic's own API reference); extra_body
    # is the SDK's documented mechanism for passing a real API field that
    # isn't (or is no longer) in the method's typed parameter list —
    # confirmed live this actually reaches the API (got a real
    # authentication_error response back, not another TypeError).
    response = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        extra_body={"temperature": config.llm_temperature},
        system=config.static_system_prompt(),
        messages=_anthropic_messages(messages),
        tools=_anthropic_tools(tools),
    )
    return _parse_anthropic_message(response)


def _anthropic_chat_streaming(messages: list[dict], tools: list[dict]):
    client = _get_anthropic_client()
    logger.debug("Streaming from Anthropic (%s) with %d messages", config.model, len(messages))

    def generator():
        # See _anthropic_chat's comment — same SDK-version fix.
        with client.messages.stream(
            model=config.model,
            max_tokens=config.max_tokens,
            extra_body={"temperature": config.llm_temperature},
            system=config.static_system_prompt(),
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
# Gemini backend — translated to/from Ollama's shape
# ----------------------------------------------------------------------

def _gemini_tools(tools: list[dict]) -> gtypes.Tool:
    """This app's tool schemas are built once in OpenAI/Ollama shape
    (tools/__init__.py); translate to Gemini's FunctionDeclaration shape."""
    declarations = [
        gtypes.FunctionDeclaration(
            name=t["function"]["name"],
            description=t["function"]["description"],
            parameters_json_schema=t["function"]["parameters"],
        )
        for t in tools
    ]
    return gtypes.Tool(function_declarations=declarations)


def _gemini_contents(messages: list[dict]) -> list[gtypes.Content]:
    """
    Translate this app's flat history (role: user/assistant/tool, tool
    entries carrying a `name`) into Gemini's Content list.

    Past tool calls are folded into plain text on a "model"-role Content
    rather than replayed as structured function_call/function_response
    parts. Gemini 3's models attach a `thought_signature` (an opaque,
    server-verified token) to every function_call part they generate, and
    reject a function_call part on the *next* request if that signature is
    missing or not genuine — confirmed live: even a placeholder signature
    is rejected outright ("Corrupted thought signature"), not just a soft
    warning. This app's history only ever persisted a tool's name and
    result (never its arguments, and now never a signature either — no
    schema stores one), so a structured replay can never carry a real
    signature. Describing the exchange as text sidesteps the requirement
    entirely — Gemini doesn't need a signature to read a summary of what
    already happened, only to accept a function_call block as one to
    execute again.

    ("tool" is also not a valid Gemini content role, despite appearing in
    some docs examples — confirmed live: the API rejects it with "Role
    'tool' is not supported" — but that's moot here since results are
    folded into the text summary rather than sent as their own Content.)

    The fold keeps role="model" — attributing the action to the assistant,
    not the user — even though an earlier version of this function used
    role="user" here to dodge a separate Gemini requirement (see below).
    That earlier version caused a confirmed-live bug: framing "I called X
    and got Y" as something the *user* said, rather than the model, made
    the model lose track of having already acted — it re-called the same
    tool 8 times in a row (hitting max_tool_iterations) instead of
    recognizing the result and answering. role="model" fixed it.
    """
    result: list[gtypes.Content] = []
    i, n = 0, len(messages)

    while i < n:
        msg = messages[i]

        if msg["role"] == "user":
            result.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=msg["content"])]))
            i += 1
            continue

        if msg["role"] != "assistant":
            # Defensive fallback for a lone "tool" message with no preceding
            # assistant turn — shouldn't happen given how loop.py builds history.
            result.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=str(msg["content"]))]))
            i += 1
            continue

        text = msg["content"]
        tool_msgs = []
        j = i + 1
        while j < n and messages[j]["role"] == "tool":
            tool_msgs.append(messages[j])
            j += 1

        if not tool_msgs:
            result.append(gtypes.Content(role="model", parts=[gtypes.Part.from_text(text=text or "(no response)")]))
            i += 1
            continue

        lines = [text] if text else []
        for t in tool_msgs:
            lines.append(f"[I called {t.get('name', 'tool')} and got: {t['content']}]")
        result.append(gtypes.Content(role="model", parts=[gtypes.Part.from_text(text="\n".join(lines))]))
        i = j

    # Gemini separately rejects a request whose *last* content has role
    # "model" ("Requests ending with a model turn are not supported"),
    # which the content above often is when continuing the same turn right
    # after a tool executes, before any new real user message exists yet.
    # A tiny synthetic nudge satisfies that without reintroducing the
    # role="user" misattribution bug this function used to have.
    if result and result[-1].role == "model":
        result.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text="Please continue.")]))

    return result


def _parse_gemini_response(response: gtypes.GenerateContentResponse) -> dict:
    """Normalize a Gemini GenerateContentResponse into this app's {message:
    {content, tool_calls}} shape."""
    tool_calls = [
        {"function": {"name": fc.name, "arguments": dict(fc.args or {})}}
        for fc in (response.function_calls or [])
    ]
    return {"message": {"content": response.text or "", "tool_calls": tool_calls}}


def _gemini_chat(messages: list[dict], tools: list[dict]):
    client = _get_gemini_client()
    logger.debug("Calling Gemini (%s) with %d messages", config.gemini_model, len(messages))
    config_kwargs = {"system_instruction": config.static_system_prompt(), "temperature": config.llm_temperature}
    if config.active_model_supports_tools:
        config_kwargs["tools"] = [_gemini_tools(tools)]
    response = client.models.generate_content(
        model=config.gemini_model,
        contents=_gemini_contents(messages),
        config=gtypes.GenerateContentConfig(**config_kwargs),
    )
    return _parse_gemini_response(response)


def _gemini_chat_streaming(messages: list[dict], tools: list[dict]):
    client = _get_gemini_client()
    logger.debug("Streaming from Gemini (%s) with %d messages", config.gemini_model, len(messages))
    config_kwargs = {"system_instruction": config.static_system_prompt(), "temperature": config.llm_temperature}
    if config.active_model_supports_tools:
        config_kwargs["tools"] = [_gemini_tools(tools)]

    def generator():
        # Gemini's stream has no Anthropic-style get_final_message() helper,
        # so tool calls are tracked as they appear across chunks rather than
        # read from one aggregated final object — last chunk with any
        # function_calls wins, since a tool call ends that turn's generation.
        tool_calls: list[dict] = []
        for chunk in client.models.generate_content_stream(
            model=config.gemini_model,
            contents=_gemini_contents(messages),
            config=gtypes.GenerateContentConfig(**config_kwargs),
        ):
            if chunk.text:
                yield {"message": {"content": chunk.text}}
            if chunk.function_calls:
                tool_calls = [
                    {"function": {"name": fc.name, "arguments": dict(fc.args or {})}}
                    for fc in chunk.function_calls
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
    if config.llm_provider == "gemini":
        return _gemini_chat(messages, tools)
    return _ollama_chat(messages, tools)


def call_llm_streaming(messages: list[dict], tools: list[dict]):
    """
    Streaming call - yields chunks as they arrive.
    Note: tool calls arrive in the final chunk, so the caller must accumulate.
    """
    if config.llm_provider == "anthropic":
        return _anthropic_chat_streaming(messages, tools)
    if config.llm_provider == "gemini":
        return _gemini_chat_streaming(messages, tools)
    return _ollama_chat_streaming(messages, tools)
