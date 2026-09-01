"""FU-122 -- the salvage must be REACHED, not merely present.

The ledger's most repeated scar (REACHABILITY_POSTMORTEM, FU-102, FU-116) is a
fix that merges and never becomes reachable. A parser that works in isolation
while the adapter never calls it would reproduce exactly the failure this fix
exists to end, and would look green doing it. So this drives the real adapter.
"""
import json
import sys
import types

import escalation


class _Resp:
    status_code = 200
    headers = {}

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_requests(captured, content):
    mod = types.ModuleType("requests")

    def post(url, **kw):
        captured["payload"] = kw.get("json")
        return _Resp(content)

    mod.post = post
    return mod


def _spec():
    return escalation.ModelSpec(
        backend="openai_compatible", model_id="zo-ladder-groq",
        context_window=32000, rpm_limit=30, cost_priority=0.0,
        label="test-rung",
        base_url="https://example.invalid/v1", key_env="TEST_KEY",
    )


TOOLS = [{"type": "function",
          "function": {"name": "zo_directive_bridge__propose_directive",
                       "parameters": {"type": "object", "properties": {}}}}]

FENCED = (
    "I'll propose one service.\n\n"
    '```python\n'
    'zo_directive_bridge__propose_directive(\n'
    '    task="build_service_risk_tier_trend",\n'
    '    handler="build_service",\n'
    '    description="GET /api/risk/trend?days=N. ACCEPTANCE: ... prints PASS.",\n'
    '    complexity="medium",\n'
    ')\n'
    '```\n'
)


def test_the_adapter_returns_tool_calls_for_a_fenced_python_call(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "x")
    captured = {}
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(captured, FENCED))

    text, err, tool_calls = escalation._call_openai_compatible(
        _spec(), "prompt", None, 1024, 0.2, tools=TOOLS)

    assert err is None
    assert tool_calls, "the adapter must hand goose a STRUCTURED call, not prose"
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"
    args = json.loads(tool_calls[0]["function"]["arguments"])
    assert args["task"] == "build_service_risk_tier_trend"
    assert args["handler"] == "build_service"
    # the consumed fence is stripped from the text goose is shown
    assert "```" not in (text or "")


def test_a_structured_tool_call_is_never_second_guessed(monkeypatch):
    """If the provider gave us a real tool_calls field, salvage must not run.
    Double-proposing is a distinct failure and this ordering prevents it."""
    monkeypatch.setenv("TEST_KEY", "x")
    real = {"id": "call_real", "type": "function",
            "function": {"name": "zo_directive_bridge__propose_directive",
                         "arguments": '{"task": "from_structured_field"}'}}

    mod = types.ModuleType("requests")

    class R(_Resp):
        def json(self):
            return {"choices": [{"message": {"content": FENCED,
                                             "tool_calls": [real]}}]}

    mod.post = lambda url, **kw: R(FENCED)
    monkeypatch.setitem(sys.modules, "requests", mod)

    _text, err, tool_calls = escalation._call_openai_compatible(
        _spec(), "prompt", None, 1024, 0.2, tools=TOOLS)

    assert err is None
    assert tool_calls == [real], "structured field wins; salvage must not fire"


def test_ordinary_prose_with_no_fence_is_unchanged(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "x")
    captured = {}
    plain = "The gaps map is exhausted; nothing to propose."
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(captured, plain))

    text, err, tool_calls = escalation._call_openai_compatible(
        _spec(), "prompt", None, 1024, 0.2, tools=TOOLS)

    assert err is None and tool_calls is None
    assert text == plain
