"""Tests for MiniMax native tool-call envelope -> OpenAI tool_calls parsing
(escalation._parse_minimax_tool_calls). Regression cover for the 2026-06-18
100% ghost stall: MiniMax emitted <minimax:tool_call> XML in content instead of
the structured tool_calls field, so Goose's developer extension never fired."""
import json
from escalation import _parse_minimax_tool_calls


def test_parses_single_invoke_write():
    content = (
        '<minimax:tool_call>\n<invoke name="text_editor">\n'
        '<parameter name="command">write</parameter>\n'
        '<parameter name="path">/home/workspace/zo_sentinel/foo.py</parameter>\n'
        '<parameter name="file_text">print("hi")</parameter>\n'
        '</invoke>\n</minimax:tool_call>'
    )
    tcs, residual = _parse_minimax_tool_calls(content)
    assert tcs and len(tcs) == 1
    fn = tcs[0]["function"]
    assert tcs[0]["type"] == "function" and tcs[0]["id"].startswith("call_")
    assert fn["name"] == "text_editor"
    args = json.loads(fn["arguments"])
    assert args == {"command": "write",
                    "path": "/home/workspace/zo_sentinel/foo.py",
                    "file_text": 'print("hi")'}
    assert residual == ""


def test_multiple_invokes_and_residual_prose():
    content = (
        'Let me look first.\n<minimax:tool_call>\n'
        '<invoke name="Read"><parameter name="file_path">/a/b.md</parameter></invoke>\n'
        '<invoke name="Glob"><parameter name="pattern">*.py</parameter>'
        '<parameter name="root">/home/workspace</parameter></invoke>\n'
        '</minimax:tool_call>'
    )
    tcs, residual = _parse_minimax_tool_calls(content)
    assert len(tcs) == 2
    assert tcs[0]["function"]["name"] == "Read"
    assert json.loads(tcs[1]["function"]["arguments"]) == {
        "pattern": "*.py", "root": "/home/workspace"}
    assert residual == "Let me look first."


def test_plain_text_untouched():
    tcs, residual = _parse_minimax_tool_calls("a normal answer, no tools")
    assert tcs is None and residual == "a normal answer, no tools"


def test_empty_and_none_safe():
    assert _parse_minimax_tool_calls("") == (None, "")
    assert _parse_minimax_tool_calls(None) == (None, None)
