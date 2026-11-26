"""GR-6 acceptance harness, emitted by the protean bridge (zo-fleet-tools
protean_bridge.py, directive eval_gr_6; first emitted as tests/acceptance/, moved here because tools/ci_pytest.py only collects tests/test_*.py, chairman/QUEUE.md item 2).

Gap: UNKNOWN never escalates. A predicate whose probe cannot evaluate (bus
down, catalog error, missing table, rc >= 2) is recorded UNKNOWN by
tools/fu/fu_verify.py and may rest there forever, which reads exactly like a
quiet day. The queue's evidence for the move is "a test that a catalog error
cannot rest as UNKNOWN past budget".

This file must be RED on a tree without the remedy (two-pole red-before) and
GREEN once fu_verify carries an UNKNOWN age budget. One test per symptom id.

Contract asserted (the interface the BUILD step must provide):
  * state[fu]["unknown_since"]: ISO stamp of the first UNKNOWN in the current
    unbroken UNKNOWN streak; cleared by any GREEN or RED.
  * sweep(lines, state, clock=<aware datetime>) uses `clock` as "now".
  * an UNKNOWN older than fu_verify.UNKNOWN_AGE_BUDGET_H gets
    action "ESCALATE-UNKNOWN" and "unknown_age_h" on its row.
  * escalation never closes, reopens or stamps a ledger entry, and never
    touches the green streak (UNKNOWN is still not evidence of either pole).
  * main() exits 5 when any predicate is over budget, so the scheduled task's
    result code carries the escalation.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FU = ROOT / "tools" / "fu"
sys.path.insert(0, str(FU))

import fu_verify  # noqa: E402

CLOCK = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger(cmd="exit 2", fu_id="904"):
    # rc 2 is the sql_assert UNKNOWN sentinel (fu_seed_predicates.sql_assert):
    # the shape a catalog error / nonexistent table arrives in.
    return ("### FU-%s | catalog error\n"
            "- date: 2026-07-28 - source: t - status: open - priority: P1\n"
            "- class: defect\n- detail: d\n- verify: `%s`\n"
            "- verify_seen_red: 2026-07-01T00:00:00Z\n- log:\n- resolution:\n" % (fu_id, cmd)).split("\n")


def _row(results, fu_id="FU-904"):
    return [r for r in results if r["fu"] == fu_id][0]


def test_B_budget_is_declared():
    assert isinstance(getattr(fu_verify, "UNKNOWN_AGE_BUDGET_H", None), (int, float))
    assert 0 < fu_verify.UNKNOWN_AGE_BUDGET_H <= 7 * 24


def test_B_first_unknown_starts_the_clock():
    state = {}
    results, mutations = fu_verify.sweep(_ledger(), state, clock=CLOCK)
    assert _row(results)["verdict"] == "UNKNOWN"
    assert state["FU-904"]["unknown_since"] == _iso(CLOCK)
    assert _row(results)["action"] != "ESCALATE-UNKNOWN"
    assert mutations == []


def test_B_unknown_within_budget_does_not_escalate():
    b = fu_verify.UNKNOWN_AGE_BUDGET_H
    state = {"FU-904": {"greens": 1, "history": [],
                        "unknown_since": _iso(CLOCK - timedelta(hours=b - 1))}}
    results, _ = fu_verify.sweep(_ledger(), state, clock=CLOCK)
    assert _row(results)["action"] != "ESCALATE-UNKNOWN"


def test_B_catalog_error_cannot_rest_unknown_past_budget():
    b = fu_verify.UNKNOWN_AGE_BUDGET_H
    since = CLOCK - timedelta(hours=b + 1)
    state = {"FU-904": {"greens": 1, "history": [], "unknown_since": _iso(since)}}
    results, mutations = fu_verify.sweep(_ledger(), state, clock=CLOCK)
    row = _row(results)
    assert row["verdict"] == "UNKNOWN"
    assert row["action"] == "ESCALATE-UNKNOWN"
    assert row["unknown_age_h"] > b
    # the streak start is kept, not reset, by a further UNKNOWN
    assert state["FU-904"]["unknown_since"] == _iso(since)
    # escalation is not a verdict: no ledger mutation, green streak untouched
    assert mutations == []
    assert state["FU-904"]["greens"] == 1


def test_B_a_real_verdict_clears_the_clock():
    for cmd in ("exit 0", "exit 1"):
        state = {"FU-904": {"greens": 0, "history": [],
                            "unknown_since": _iso(CLOCK - timedelta(days=30))}}
        results, _ = fu_verify.sweep(_ledger(cmd), state, clock=CLOCK)
        assert _row(results)["action"] != "ESCALATE-UNKNOWN"
        assert "unknown_since" not in state["FU-904"]


def test_B_main_exit_code_carries_the_escalation(tmp_path):
    ledger = tmp_path / "FOLLOWUPS.md"
    ledger.write_text("\n".join(_ledger()), encoding="utf-8")
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"FU-904": {"greens": 0, "history": [],
                                         "unknown_since": "2026-01-01T00:00:00Z"}}), encoding="utf-8")
    p = subprocess.run([sys.executable, str(FU / "fu_verify.py"), "--ledger", str(ledger),
                        "--state", str(st), "--dry-run", "--json"],
                       capture_output=True, text=True, timeout=120, cwd=str(FU))
    assert p.returncode == 5, p.stdout[-800:] + p.stderr[-800:]
    out = json.loads(p.stdout)
    assert out["unknown_over_budget"] == ["FU-904"]


def test_B_no_escalation_means_rc_0(tmp_path):
    ledger = tmp_path / "FOLLOWUPS.md"
    ledger.write_text("\n".join(_ledger("exit 0")), encoding="utf-8")
    p = subprocess.run([sys.executable, str(FU / "fu_verify.py"), "--ledger", str(ledger),
                        "--state", str(tmp_path / "s.json"), "--dry-run", "--json"],
                       capture_output=True, text=True, timeout=120, cwd=str(FU))
    assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
    assert json.loads(p.stdout)["unknown_over_budget"] == []
