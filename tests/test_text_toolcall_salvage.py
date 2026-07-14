"""The architect's proposals must not evaporate.

Caught live 2026-07-14 17:09 on zo-ladder-nvidia. The architect proposed a
directive as its FIRST action -- exactly as the propose-first recipe (#1472)
demands -- and it vanished:

    [TOOL_CALLS]zo_directive_bridge__propose_directive{"task": "...", ...}

Mistral-lineage models (NVIDIA NIM, Cerebras, Groq, Mistral) emit tool calls as
LITERAL TEXT with a [TOOL_CALLS] marker instead of the structured OpenAI
tool_calls array. goose acts only on structured tool_calls, so the call was
silently dropped and the cycle scored +0.

MiniMax had the identical bug (#251), but that salvage only speaks
<minimax:tool_call> XML and lives inside _call_minimax_direct. Every capable rung
runs through the OpenAI-compatible adapter, which had NO salvage at all.
"""
import json

import escalation


def test_the_exact_live_failure_is_salvaged():
    """Verbatim shape from the 17:09 architect transcript."""
    content = (
        'I will propose one directive.\n'
        '[TOOL_CALLS]zo_directive_bridge__propose_directive'
        '{"task": "build_verdict_breakdown_api", "handler": "generate_file", '
        '"output_file": "verdict_breakdown_api.py", "complexity": "medium", '
        '"description": "FastAPI router exposing GET /verdicts/{server_id}/breakdown."}'
    )
    calls, residual = escalation._parse_text_tool_calls(content)

    assert calls, "the proposal must NOT evaporate"
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["task"] == "build_verdict_breakdown_api"
    assert args["output_file"] == "verdict_breakdown_api.py"
    assert calls[0]["type"] == "function" and calls[0]["id"].startswith("call_")
    assert "[TOOL_CALLS]" not in residual
    assert "I will propose one directive." in residual


def test_braces_and_quotes_inside_a_description_do_not_truncate():
    """Descriptions routinely contain braces and quotes ({server_id}, JSON shapes).
    A naive regex mis-terminates and silently truncates the call."""
    content = (
        '[TOOL_CALLS]propose_directive'
        '{"task": "t", "description": "returns {axes: {axis: {label, p_top}}} '
        'and a \\"quoted\\" phrase", "complexity": "low"}'
    )
    calls, _ = escalation._parse_text_tool_calls(content)
    assert calls
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["task"] == "t" and args["complexity"] == "low"
    assert "p_top" in args["description"]


def test_multiple_calls_in_one_message():
    content = (
        '[TOOL_CALLS]propose_directive{"task": "a"}'
        '[TOOL_CALLS]propose_directive{"task": "b"}'
    )
    calls, residual = escalation._parse_text_tool_calls(content)
    assert len(calls) == 2
    assert [json.loads(c["function"]["arguments"])["task"] for c in calls] == ["a", "b"]
    assert residual == ""


def test_json_array_shape():
    content = ('[TOOL_CALLS][{"name": "propose_directive", '
               '"arguments": {"task": "arr", "complexity": "low"}}]')
    calls, _ = escalation._parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "propose_directive"
    assert json.loads(calls[0]["function"]["arguments"])["task"] == "arr"


def test_no_marker_is_a_no_op():
    """A rung that behaves correctly must be completely unaffected."""
    content = "Just prose about what I might propose."
    calls, residual = escalation._parse_text_tool_calls(content)
    assert calls is None and residual == content


def test_malformed_payload_never_raises():
    for bad in ("[TOOL_CALLS]name{not json",
                "[TOOL_CALLS]",
                "[TOOL_CALLS]{}",
                "[TOOL_CALLS]name{"):
        calls, residual = escalation._parse_text_tool_calls(bad)
        assert calls is None            # dropped, but no crash
        assert residual == bad
