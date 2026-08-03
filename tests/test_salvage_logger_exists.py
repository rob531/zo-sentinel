"""FU-123 -- the salvages failed ONLY WHEN THEY WORKED.

Found 2026-07-27 while implementing FU-122, by driving the real adapter instead
of only unit-testing the new parser.

`escalation.py` called `log.warning(...)` from all three tool-call salvage
branches of `_call_openai_compatible` -- and never defined or imported `log`.
Those branches sit inside the function's blanket `except Exception as e:
return None, f"{type(e).__name__}: {e}", None`. So the control flow was:

    model emits [TOOL_CALLS]... as text
      -> parser recovers the call            (worked -- unit tests proved it)
      -> log.warning(...)                    -> NameError
      -> except Exception                    -> return (None, "NameError: ...")
      -> ladder reads an errored rung        -> ROTATE
      -> N rotations                         -> ARCHITECT NON-CONVERGENCE
      -> STARVATION FLOOR: "This needs a human"

A parser that fails drops one call. A parser that fails *only on success*
converts every recovery into a rung rotation and eventually into a request for
human intervention. PR #1635 shipped three correct parsers behind an exception
that guaranteed none could ever deliver one, and every unit test stayed green
because none of them called the adapter.

The generalisable lesson, which is why these tests drive `_call_openai_compatible`
and not the parsers: a unit test of the component proves the component. Only the
call path proves the FIX. This is the same reachability scar as FU-102 and
FU-116 -- merged, present, unreachable -- wearing a different costume.
"""
import json
import sys
import types

import escalation


def test_the_module_has_the_logger_its_salvage_branches_call():
    assert hasattr(escalation, "log"), (
        "escalation.log is referenced by three salvage branches; without it "
        "every successful salvage raises NameError inside the adapter's "
        "except-block and becomes a rung rotation"
    )
    escalation.log.warning("smoke: %s", "callable")   # must not raise


class _Resp:
    status_code = 200
    headers = {}

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _spec():
    return escalation.ModelSpec(
        backend="openai_compatible", model_id="zo-ladder-nvidia",
        context_window=32000, rpm_limit=30, cost_priority=0.0,
        label="test-rung", base_url="https://example.invalid/v1",
        key_env="TEST_KEY",
    )


def _drive(monkeypatch, content, tools=None):
    monkeypatch.setenv("TEST_KEY", "x")
    mod = types.ModuleType("requests")
    mod.post = lambda url, **kw: _Resp(content)
    monkeypatch.setitem(sys.modules, "requests", mod)
    return escalation._call_openai_compatible(
        _spec(), "prompt", None, 1024, 0.2, tools=tools)


def test_shape_1_tool_calls_marker_reaches_goose_as_a_structured_call(monkeypatch):
    """The FU-002 / #1635 shape. Green in unit tests since 7/19; this is the
    first test that proves it survives the adapter."""
    content = ('I will propose one directive.\n'
               '[TOOL_CALLS]zo_directive_bridge__propose_directive'
               '{"task": "build_verdict_breakdown_api", "handler": "generate_file"}')
    text, err, tool_calls = _drive(monkeypatch, content)

    assert err is None, f"a SUCCESSFUL salvage must not error the turn (got {err!r})"
    assert tool_calls and len(tool_calls) == 1
    assert json.loads(tool_calls[0]["function"]["arguments"])["task"] == \
        "build_verdict_breakdown_api"


def test_shape_3_prose_TOOL_marker_reaches_goose_as_a_structured_call(monkeypatch):
    content = ("I'll call the bridge now.\n"
               "TOOL: zo_directive_bridge__propose_directive\n"
               '{"task": "build_service_x", "handler": "build_service"}\n')
    _text, err, tool_calls = _drive(monkeypatch, content)

    assert err is None, f"a SUCCESSFUL salvage must not error the turn (got {err!r})"
    assert tool_calls and len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"


def test_a_fabricated_TOOL_result_still_fails_the_turn_on_purpose(monkeypatch):
    """The one branch that SHOULD error. Fixing the logger must not soften it:
    a model roleplaying the bridge's reply has produced no actionable call, and
    the turn must fail so the rung rotates instead of wedging on fake results."""
    content = 'TOOL: {"status": "ok", "directive_id": 4711}\n'
    _text, err, tool_calls = _drive(monkeypatch, content)

    assert tool_calls is None
    assert err and "hallucinated" in err


def test_no_salvage_path_can_raise_NameError(monkeypatch):
    """Blanket guard: the adapter's except-block turns ANY internal bug into a
    plausible-looking rung failure, so it must never be the thing that reports
    a defect in our own code."""
    for content in (
        '[TOOL_CALLS]zo_directive_bridge__propose_directive{"task": "t"}',
        "TOOL: zo_directive_bridge__propose_directive\n",
        '```python\nzo_directive_bridge__propose_directive(task="t")\n```',
    ):
        _text, err, _tc = _drive(monkeypatch, content)
        assert not (err and "NameError" in err), f"NameError leaked for {content!r}"
