"""Gate attribution in the builder's completion chain (chairman review 2026-07-20).

Context / why this test exists
------------------------------
goose_runner's completion chain has seven distinct ways to reject a build, but
every rejection used to be written to build_provenance with ONE hardcoded
string: "ghost build: declared output_file was not produced". On 2026-07-20,
17 of 37 builds were recorded as ghosts under that message. The runtime log
showed 12 of them were actually `_edit_diff_gate` rejections -- edit-class
(wire_*/integrate_*) builds that changed nothing -- which is the direct signal
for the 246 unmounted routers found in the 2026-07-19 reachability postmortem.
The ledger could not distinguish them, so the census and the ladder eval were
reasoning off one undifferentiated bucket.

These tests pin the two properties that matter:
  1. every gate produces a DISTINCT, attributable ledger string; and
  2. the chain's short-circuit ORDER is unchanged, so attribution is a pure
     observability gain and not a behaviour change.

The gate helpers are imported without importing goose_runner itself (which
starts daemons and touches the mesh on import); we load the module source and
exec only the pure helpers.
"""

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNNER = REPO / "goose_runner.py"

PURE_NAMES = {"GATE_REASONS", "DEFAULT_GATE", "gate_error_text", "_gate_chain"}


def _load_pure_helpers():
    """Exec ONLY the gate-attribution helpers out of goose_runner.py.

    Importing goose_runner has side effects (daemon wiring, mesh writes), so we
    parse the file and re-exec just the definitions under test. This keeps the
    test honest -- it runs the real source, not a copy -- without booting the
    factory.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in PURE_NAMES:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in PURE_NAMES:
                    keep.append(node)
                    break
    module = ast.Module(body=keep, type_ignores=[])
    ns = {}
    exec(compile(module, str(RUNNER), "exec"), ns)  # noqa: S102 - test harness
    missing = PURE_NAMES - set(ns)
    if missing:
        pytest.fail(f"goose_runner.py no longer defines: {sorted(missing)}")
    return ns


NS = _load_pure_helpers()
GATE_REASONS = NS["GATE_REASONS"]
gate_error_text = NS["gate_error_text"]
_gate_chain = NS["_gate_chain"]

# The chain's short-circuit order. Attribution must not reorder the gates:
# output existence is checked before the edit-diff guard, and the expensive
# self-test runs last.
EXPECTED_ORDER = [
    "engine_failed",
    "output_missing",
    "edit_diff",
    "syntax",
    "schema_prm",
    "no_hollow",
    "selftest",
]


def _install_gates(monkeypatch, failing=None):
    """Point _gate_chain's globals at stubs; `failing` names the gate that says no."""
    calls = []

    def stub(name, result_when_ok=True):
        def _fn(*_args, **_kwargs):
            calls.append(name)
            return result_when_ok and name != failing
        return _fn

    NS["output_confirmed"] = stub("output_missing")
    NS["_edit_diff_gate"] = stub("edit_diff")
    NS["_syntax_gate"] = stub("syntax")
    NS["_schema_prm_gate"] = stub("schema_prm")
    NS["_no_hollow_gate"] = stub("no_hollow")
    NS["_selftest_gate"] = stub("selftest")
    return calls


def test_every_gate_has_a_distinct_reason():
    """No two gates may share a ledger string -- that is the bug being fixed."""
    assert set(GATE_REASONS) == set(EXPECTED_ORDER)
    reasons = list(GATE_REASONS.values())
    assert len(set(reasons)) == len(reasons), "gate reasons must be distinct"


@pytest.mark.parametrize("gate", EXPECTED_ORDER)
def test_each_gate_is_attributable_in_the_ledger_string(gate):
    text = gate_error_text(gate)
    assert text.startswith(f"gate={gate}:"), text
    assert GATE_REASONS[gate] in text


def test_unknown_gate_is_honestly_unattributed():
    """An unrecognised gate must NOT silently claim the output was missing.

    Claiming a specific wrong cause is what made the old ledger unusable; an
    unknown gate has to look unknown.
    """
    text = gate_error_text("some_new_gate")
    assert "unattributed" in text
    assert "output_file was not produced" not in text


def test_none_gate_falls_back_to_output_missing():
    """Legacy callers passing no gate keep the historical meaning."""
    assert gate_error_text(None) == f"gate=output_missing: {GATE_REASONS['output_missing']}"


def test_engine_failure_short_circuits_before_any_gate_runs(monkeypatch):
    calls = _install_gates(monkeypatch)
    ok, gate = _gate_chain({}, "d1", None, engine_ok=False)
    assert (ok, gate) == (False, "engine_failed")
    assert calls == [], "no gate should run when the engine itself failed"


def test_all_gates_passing_returns_no_failing_gate(monkeypatch):
    calls = _install_gates(monkeypatch)
    ok, gate = _gate_chain({}, "d1", None, engine_ok=True)
    assert (ok, gate) == (True, None)
    assert calls == EXPECTED_ORDER[1:], "gate order changed"


@pytest.mark.parametrize("failing", EXPECTED_ORDER[1:])
def test_failing_gate_is_reported_and_short_circuits(monkeypatch, failing):
    calls = _install_gates(monkeypatch, failing=failing)
    ok, gate = _gate_chain({}, "d1", None, engine_ok=True)
    assert ok is False
    assert gate == failing
    # Short-circuit: the failing gate runs last, nothing after it runs.
    assert calls[-1] == failing
    expected_prefix = EXPECTED_ORDER[1:EXPECTED_ORDER.index(failing) + 1]
    assert calls == expected_prefix


def test_edit_class_build_is_never_reported_as_a_missing_output(monkeypatch):
    """The 2026-07-20 regression, pinned.

    An edit-class directive declares output_file=null, so output_confirmed
    trusts it and only _edit_diff_gate can reject. Such a build must be
    attributed to edit_diff -- reporting "output_file was not produced" for it
    is precisely the misattribution that hid the unmounted-router signal.
    """
    _install_gates(monkeypatch, failing="edit_diff")
    ok, gate = _gate_chain({"output_file": None}, "wire_x_into_main", None, engine_ok=True)
    assert ok is False
    assert gate == "edit_diff"
    assert "produced no edit" in gate_error_text(gate)
    assert "output_file was not produced" not in gate_error_text(gate)
