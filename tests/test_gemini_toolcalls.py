"""Gemini tool-call adapter: translate OpenAI tools <-> Gemini
functionDeclarations/functionCall so a Gemini rung can DRIVE goose's developer
extension (was text-only -> tool-calling pinned to MiniMax)."""
import json
from escalation import _openai_tools_to_gemini, _gemini_parts_to_result


def test_tools_translate_to_function_declarations():
    tools = [{"type": "function", "function": {
        "name": "write_file", "description": "w",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    g = _openai_tools_to_gemini(tools)
    assert g == [{"functionDeclarations": [{
        "name": "write_file", "description": "w",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]}]


def test_tools_empty_or_none():
    assert _openai_tools_to_gemini(None) is None
    assert _openai_tools_to_gemini([]) is None
    assert _openai_tools_to_gemini([{"type": "function", "function": {}}]) is None  # no name


def test_parse_functioncall_to_openai_tool_calls():
    parts = [{"functionCall": {"name": "write_file", "args": {"path": "a.py", "content": "x"}}}]
    text, tcs = _gemini_parts_to_result(parts)
    assert text == "" and tcs and len(tcs) == 1
    fn = tcs[0]["function"]
    assert fn["name"] == "write_file"
    assert json.loads(fn["arguments"]) == {"path": "a.py", "content": "x"}
    assert tcs[0]["type"] == "function" and tcs[0]["id"].startswith("call_")


def test_parse_text_only_no_tool_calls():
    text, tcs = _gemini_parts_to_result([{"text": "hello "}, {"text": "world"}])
    assert text == "hello world" and tcs is None


def test_parse_mixed_text_and_call():
    text, tcs = _gemini_parts_to_result([{"text": "ok"}, {"functionCall": {"name": "f", "args": {}}}])
    assert text == "ok" and len(tcs) == 1 and json.loads(tcs[0]["function"]["arguments"]) == {}
