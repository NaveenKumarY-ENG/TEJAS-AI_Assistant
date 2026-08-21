"""
Tests for the Anthropic/Gemini <-> internal-shape translations in
agent/llm_client.py. These are pure data-transformation functions — no
network calls, no live API key needed. The Ollama path isn't tested here
since it's a near-direct passthrough to the ollama SDK.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.genai import types as gtypes

from agent.llm_client import (
    _anthropic_messages,
    _anthropic_tools,
    _gemini_contents,
    _gemini_tools,
    _parse_anthropic_message,
    _parse_gemini_response,
)


def test_plain_text_history_passes_through():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = _anthropic_messages(history)
    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_single_tool_call_becomes_matched_tool_use_and_tool_result():
    history = [
        {"role": "user", "content": "what's the weather in tokyo"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "22C, clear", "name": "get_weather"},
    ]
    result = _anthropic_messages(history)

    assert result[0] == {"role": "user", "content": "what's the weather in tokyo"}

    assert result[1]["role"] == "assistant"
    tool_use_blocks = [b for b in result[1]["content"] if b["type"] == "tool_use"]
    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0]["name"] == "get_weather"
    tool_use_id = tool_use_blocks[0]["id"]

    assert result[2]["role"] == "user"
    assert result[2]["content"] == [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": "22C, clear"}
    ]


def test_roles_strictly_alternate_even_with_text_and_tool_call_together():
    """Anthropic rejects two consecutive same-role messages. An assistant
    that says something AND calls a tool in the same turn must collapse
    into one assistant message with mixed content blocks, not two."""
    history = [
        {"role": "user", "content": "check my disk space"},
        {"role": "assistant", "content": "Let me check that."},
        {"role": "tool", "content": "500GB free", "name": "get_system_info"},
        {"role": "assistant", "content": "You have 500GB free."},
    ]
    result = _anthropic_messages(history)

    roles = [m["role"] for m in result]
    for a, b in zip(roles, roles[1:]):
        assert a != b, f"consecutive same-role messages: {roles}"

    assert any(isinstance(m["content"], list) and any(b["type"] == "text" for b in m["content"]) for m in result)


def test_multiple_tool_calls_in_one_turn_get_distinct_ids():
    history = [
        {"role": "user", "content": "check weather and disk space"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "22C", "name": "get_weather"},
        {"role": "tool", "content": "500GB free", "name": "get_system_info"},
    ]
    result = _anthropic_messages(history)

    tool_use_ids = [b["id"] for b in result[1]["content"] if b["type"] == "tool_use"]
    result_ids = [b["tool_use_id"] for b in result[2]["content"]]

    assert len(set(tool_use_ids)) == 2  # distinct ids
    assert result_ids == tool_use_ids  # matched in the same order


def test_anthropic_tools_schema_translation():
    openai_shaped = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
        }
    ]
    result = _anthropic_tools(openai_shaped)
    assert result == [
        {
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        }
    ]


def test_parse_anthropic_message_extracts_text_and_tool_calls():
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Sure, checking now."),
            SimpleNamespace(type="tool_use", name="get_weather", input={"city": "Tokyo"}),
        ]
    )
    result = _parse_anthropic_message(fake_message)
    assert result == {
        "message": {
            "content": "Sure, checking now.",
            "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Tokyo"}}}],
        }
    }


# ----------------------------------------------------------------------
# Gemini translation
# ----------------------------------------------------------------------

def test_gemini_plain_text_history_becomes_user_and_model_contents():
    """Real call sites (agent/loop.py) never actually hand this function a
    history ending on assistant-text-only — _messages_for_llm always appends
    a fresh user turn, and the tool loop's post-execution rebuild always
    ends on a tool result — but the function stays correct for this shape
    too: it appends the same "continue" nudge as the tool-fold case,
    unconditionally, whenever the result would otherwise end on "model"."""
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = _gemini_contents(history)
    assert [c.role for c in result] == ["user", "model", "user"]
    assert result[0].parts[0].text == "hello"
    assert result[1].parts[0].text == "hi there"


def test_gemini_tool_call_folds_into_plain_text_model_content():
    """Past tool calls are described as text, not replayed as structured
    function_call/function_response parts — see _gemini_contents' docstring
    for why (Gemini 3's required thought_signature can't be reconstructed
    from what this app persists, and even a placeholder is rejected as a
    "Corrupted thought signature", confirmed live). The fold keeps role
    "model" (attributing the action to the assistant) plus a trailing
    synthetic "user" nudge, since Gemini separately rejects a request
    ending on a "model" turn — confirmed live, and confirmed live that the
    naive alternative (folding as role="user") makes the model lose track
    of having already acted and re-call the same tool repeatedly."""
    history = [
        {"role": "user", "content": "what's the weather in tokyo"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "22C, clear", "name": "get_weather"},
    ]
    result = _gemini_contents(history)

    assert [c.role for c in result] == ["user", "model", "user"]
    assert result[1].parts[0].function_call is None  # no structured replay
    assert "get_weather" in result[1].parts[0].text
    assert "22C, clear" in result[1].parts[0].text


def test_gemini_assistant_text_plus_tool_call_share_one_folded_content():
    """An assistant that says something AND calls a tool in the same turn
    collapses into one folded "model" Content whose text includes both."""
    history = [
        {"role": "user", "content": "check my disk space"},
        {"role": "assistant", "content": "Let me check that."},
        {"role": "tool", "content": "500GB free", "name": "get_system_info"},
    ]
    result = _gemini_contents(history)

    assert result[1].role == "model"
    assert len(result[1].parts) == 1
    text = result[1].parts[0].text
    assert "Let me check that." in text
    assert "get_system_info" in text
    assert "500GB free" in text


def test_gemini_multiple_tool_calls_in_one_turn_fold_into_one_content():
    history = [
        {"role": "user", "content": "check weather and disk space"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "22C", "name": "get_weather"},
        {"role": "tool", "content": "500GB free", "name": "get_system_info"},
    ]
    result = _gemini_contents(history)

    text = result[1].parts[0].text
    assert "get_weather" in text and "22C" in text
    assert "get_system_info" in text and "500GB free" in text


def test_gemini_no_continue_nudge_when_history_already_ends_on_user():
    """The synthetic "Please continue." nudge should only appear when the
    fold would otherwise leave the request ending on a "model" turn — not
    when a real new user message already follows a completed tool call."""
    history = [
        {"role": "user", "content": "what's the weather in tokyo"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "22C, clear", "name": "get_weather"},
        {"role": "user", "content": "and in london?"},
    ]
    result = _gemini_contents(history)

    assert [c.role for c in result] == ["user", "model", "user"]
    assert result[-1].parts[0].text == "and in london?"


def test_gemini_tools_schema_translation():
    openai_shaped = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
        }
    ]
    result = _gemini_tools(openai_shaped)
    assert len(result.function_declarations) == 1
    decl = result.function_declarations[0]
    assert decl.name == "get_weather"
    assert decl.description == "Get the weather"
    assert decl.parameters_json_schema == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }


def test_parse_gemini_response_extracts_text_and_tool_calls():
    fake_response = gtypes.GenerateContentResponse(
        candidates=[
            gtypes.Candidate(
                content=gtypes.Content(
                    role="model",
                    parts=[
                        gtypes.Part.from_text(text="Sure, checking now."),
                        gtypes.Part(function_call=gtypes.FunctionCall(name="get_weather", args={"city": "Tokyo"})),
                    ],
                )
            )
        ]
    )
    result = _parse_gemini_response(fake_response)
    assert result == {
        "message": {
            "content": "Sure, checking now.",
            "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Tokyo"}}}],
        }
    }


def test_parse_gemini_response_handles_text_only():
    fake_response = gtypes.GenerateContentResponse(
        candidates=[
            gtypes.Candidate(content=gtypes.Content(role="model", parts=[gtypes.Part.from_text(text="Hi there.")]))
        ]
    )
    result = _parse_gemini_response(fake_response)
    assert result == {"message": {"content": "Hi there.", "tool_calls": []}}
