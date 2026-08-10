"""
Tests for the Anthropic <-> internal-shape translation in agent/llm_client.py.
These are pure data-transformation functions — no network calls, no live
API key needed. The Ollama path isn't tested here since it's a near-direct
passthrough to the ollama SDK.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm_client import _anthropic_messages, _anthropic_tools, _parse_anthropic_message


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
