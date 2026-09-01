"""FU-122 -- the architect converges; the harness must stop throwing it away.

Caught live 2026-07-27. `sentinel_directive_generator_goose.log` recorded, at
02:56:30Z and 03:06:50Z:

    ARCHITECT NON-CONVERGENCE (zero_proposed): ... proposed +0 -- did NOT reach
    propose_directive (tool-call loop / over-exploration); rc=0
    architect rung ROTATION: zo-ladder-nvidia -> zo-ladder-groq after 4
    consecutive non-converged cycles
    STARVATION FLOOR: gaps map is EXHAUSTED ... This needs a human

and the transcript tail immediately above those lines contained COMPLETE,
well-formed `propose_directive(...)` calls -- emitted inside a fenced ```python
block instead of as a tool call. The model did the work on every rung. The
harness discarded it, blamed the rung, rotated, and then asked for a human.

This is the FOURTH shape of the prose-salvage scar (#1635 fixed three). The
previous three were PARTIAL calls (a marker plus JSON); this one is a fully
valid python call, which makes it the easiest of the four to recover and the
most expensive to drop.

The fixtures below are VERBATIM from the log, not reconstructions.
"""
import json

import escalation


# The tool list the architect's bridge actually offers each turn.
TOOLS = [
    {"type": "function",
     "function": {"name": "zo_directive_bridge__propose_directive",
                  "parameters": {"type": "object", "properties": {}}}},
    {"type": "function",
     "function": {"name": "zo_directive_bridge__list_directives",
                  "parameters": {"type": "object", "properties": {}}}},
]


def _args(call):
    return json.loads(call["function"]["arguments"])


def test_the_exact_live_failure_is_salvaged():
    """Verbatim 03:06:50Z transcript tail (zo-ladder-groq, rc=0, proposed +0)."""
    content = (
        "I'll begin by proposing a directive for a new service that will help "
        "with the app surface.\n\n"
        '```python\n'
        'zo_directive_bridge__propose_directive(\n'
        '    task="build_service_risk_tier_trend",\n'
        '    handler="build_service",\n'
        '    description="GET /api/risk/trend?days=N on prefix /api. logic.py '
        'reads mcp_llm_axis_scores joined to mcp_server_registry, counts '
        'per-day risk_tier transitions over N days; router.py returns {days, '
        'series: [{date, tier, count}]} via pydantic; Postgres-portable SQL, '
        'no network. ACCEPTANCE: contract seeds 3 servers with tier changes '
        'across 2 days in in-memory SQLite, asserts 200, asserts series length '
        'and one known count, prints PASS.",\n'
        '    complexity="medium",\n'
        '    phase=9,\n'
        '    priority=0.90,\n'
        '    recipe="module_from_exemplar",\n'
        '    reads=["verdict_breakdown_api.py", "app/db.py", "app/models.py"]\n'
        ')\n'
        '```\n'
    )
    calls, residual = escalation._parse_pycall_tool_calls(content, TOOLS)

    assert calls, "the architect's proposal must NOT evaporate"
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"
    assert calls[0]["type"] == "function" and calls[0]["id"].startswith("call_")

    a = _args(calls[0])
    assert a["task"] == "build_service_risk_tier_trend"
    assert a["handler"] == "build_service"          # SERVICE unit, not file unit
    assert a["phase"] == 9 and a["priority"] == 0.90
    assert a["reads"] == ["verdict_breakdown_api.py", "app/db.py", "app/models.py"]
    assert "ACCEPTANCE:" in a["description"]        # spec survives intact
    # the consumed fence is gone; the model's prose around it survives
    assert "```" not in residual
    assert "I'll begin by proposing" in residual


def test_two_calls_in_one_fence_both_survive():
    """Verbatim 02:56:30Z shape -- ONE fence carrying TWO proposals, with
    triple-quoted descriptions. Salvaging only the first would silently halve
    the architect's output, which is the same defect one layer down."""
    content = (
        "Now for some new features based on the gaps map:\n\n"
        '```python\n'
        'zo_directive_bridge__propose_directive(\n'
        '    task="build_service_risk_tier_alerts",\n'
        '    handler="build_service",\n'
        '    description="""FastAPI service for risk tier change alerts.\n'
        '    GET /api/alerts/risk-tier returns servers with recent changes.\n'
        '    ACCEPTANCE: __main__ block using TestClient asserts, prints PASS.""",\n'
        '    complexity="medium",\n'
        '    phase=9,\n'
        '    priority=0.90,\n'
        '    rationale="Needed for monitoring risk tier changes",\n'
        '    reads=["app/db.py", "app/models.py"]\n'
        ')\n'
        '\n'
        'zo_directive_bridge__propose_directive(\n'
        '    task="build_service_cve_summary",\n'
        '    handler="build_service",\n'
        '    description="""FastAPI service for CVE summaries.\n'
        '    ACCEPTANCE: __main__ block asserts counts, prints PASS.""",\n'
        '    complexity="medium",\n'
        '    phase=9,\n'
        '    priority=0.85,\n'
        '    reads=["app/db.py", "app/models.py"]\n'
        ')\n'
        '```\n'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)

    assert calls and len(calls) == 2, "both proposals in the fence must survive"
    tasks = [_args(c)["task"] for c in calls]
    assert tasks == ["build_service_risk_tier_alerts", "build_service_cve_summary"]
    assert _args(calls[1])["priority"] == 0.85
    assert "PASS" in _args(calls[0])["description"]


def test_attribute_form_resolves_to_the_offered_tool():
    """Models also write the dotted form. It names the same tool."""
    content = (
        '```python\n'
        'zo_directive_bridge.propose_directive(task="t", handler="build_service")\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls and len(calls) == 1
    assert calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"


# --- the gate must be at least as strict as it is generous ------------------

def test_illustrative_code_is_never_executed_as_a_tool_call():
    """A fence full of ordinary python is the common case. Salvaging it would
    be far worse than the starvation this fix exists to end."""
    content = (
        "Here is roughly what the router would look like:\n\n"
        '```python\n'
        'import json\n'
        'print("hello")\n'
        'json.loads("{}")\n'
        'router = APIRouter(prefix="/api")\n'
        'os.system("echo nope")\n'
        '```\n'
    )
    calls, residual = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None, "only tools the model was OFFERED may be salvaged"
    assert residual == content, "a non-salvage must not mutate the content"


def test_a_tool_name_that_was_not_offered_is_rejected():
    content = (
        '```python\n'
        'some_other_bridge__delete_everything(target="prod")\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None


def test_a_non_literal_argument_rejects_the_WHOLE_call():
    """We never guess. A call whose description is a variable, an f-string or
    a concatenation is unsalvageable, and half a directive is worse than none
    -- the builder would build against a spec nobody wrote."""
    content = (
        '```python\n'
        'zo_directive_bridge__propose_directive(\n'
        '    task="build_service_x",\n'
        '    description=SPEC_TEXT,\n'
        '    handler="build_service",\n'
        ')\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None


def test_positional_arguments_reject_the_call():
    content = (
        '```python\n'
        'zo_directive_bridge__propose_directive("build_service_x", handler="build_service")\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None


def test_an_unlabelled_fence_is_not_a_python_fence():
    """Only ```python / ```py. An untagged fence is usually JSON or output."""
    content = (
        '```\n'
        'zo_directive_bridge__propose_directive(task="t", handler="build_service")\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None


def test_a_syntactically_broken_fence_is_skipped_not_raised():
    """A truncated transcript tail must never take the shim down."""
    content = (
        '```python\n'
        'zo_directive_bridge__propose_directive(task="t",\n'
        '```'
    )
    calls, residual = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None and residual == content


def test_a_nested_call_is_not_an_invocation():
    """Only top-level expression statements count."""
    content = (
        '```python\n'
        'result = wrap(zo_directive_bridge__propose_directive(task="t"))\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None


def test_no_fence_at_all_is_an_exact_passthrough():
    content = "The queue is empty; I will not propose anything this cycle."
    calls, residual = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls is None and residual == content


def test_empty_and_none_content_never_raise():
    assert escalation._parse_pycall_tool_calls("", TOOLS) == (None, "")
    assert escalation._parse_pycall_tool_calls(None, TOOLS) == (None, None)


def test_without_a_tool_list_the_namespacing_convention_gates_it():
    """When no tool list is in scope we fall back to goose's `ns__tool`
    convention -- strict enough that bare python (print, json.loads) is
    still rejected."""
    content = (
        '```python\n'
        'print("x")\n'
        'zo_directive_bridge__propose_directive(task="t", handler="build_service")\n'
        '```'
    )
    calls, _ = escalation._parse_pycall_tool_calls(content, None)
    assert calls and len(calls) == 1
    assert calls[0]["function"]["name"] == "zo_directive_bridge__propose_directive"


def test_only_the_consumed_fence_is_stripped():
    """A turn can mix an illustrative fence with a real call. The illustrative
    one must stay in the text goose shows, or we corrupt the transcript."""
    content = (
        "Sketch:\n\n"
        '```python\n'
        'def handler():\n'
        '    return 1\n'
        '```\n\n'
        "Now the proposal:\n\n"
        '```python\n'
        'zo_directive_bridge__propose_directive(task="t", handler="build_service")\n'
        '```\n'
    )
    calls, residual = escalation._parse_pycall_tool_calls(content, TOOLS)
    assert calls and len(calls) == 1
    assert "def handler():" in residual, "the illustrative fence must survive"
    assert "propose_directive" not in residual, "the consumed fence must not"


def test_the_other_three_shapes_still_behave_exactly_as_before():
    """Regression guard: this is an ADDITIVE fourth shape. The [TOOL_CALLS]
    and TOOL:-prose salvages must be untouched."""
    tc = ('[TOOL_CALLS]zo_directive_bridge__propose_directive'
          '{"task": "build_x", "handler": "generate_file"}')
    calls, _ = escalation._parse_text_tool_calls(tc)
    assert calls and _args(calls[0])["task"] == "build_x"

    prose = "TOOL: zo_directive_bridge__propose_directive\n"
    pcalls, _, nfake = escalation._parse_prose_tool_calls(prose)
    assert pcalls and nfake == 0
