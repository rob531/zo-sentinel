"""FU-141 regression: the triage sweep must not condemn a PR for its own kill.

Both assertions were confirmed RED against the pre-fix _gate_state before this
file was committed (cancelled-triage -> "failure" -> triage:stale -> auto-merge
never arms). See the FU-141 ledger entry for the 58-PR backlog it produced.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.pr_triage import _gate_state, classify

GREEN = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"}
         for n in ("pytest", "pr-gates", "no-hollow", "schema-prm", "capmap-check")]


def test_own_cancelled_check_is_not_a_gate_failure():
    rollup = GREEN + [{"name": "triage", "status": "COMPLETED",
                       "conclusion": "CANCELLED"}]
    assert _gate_state(rollup) == "success"


def test_a_foreign_cancelled_check_is_pending_not_failure():
    # A kill carries no verdict about the PR, whoever cancelled it.
    rollup = GREEN + [{"name": "pytest-extra", "status": "COMPLETED",
                       "conclusion": "CANCELLED"}]
    assert _gate_state(rollup) == "pending"


def test_a_real_failure_still_fails():
    rollup = GREEN + [{"name": "pytest", "status": "COMPLETED",
                       "conclusion": "FAILURE"}]
    assert _gate_state(rollup) == "failure"


def test_backlog_pr_reaches_solid_again():
    pr = {"number": 2164, "title": "build: scaffold_facet_enum_init",
          "mergeable": "MERGEABLE",
          "files": [{"path": "services/staged/facet_enum/__init__.py",
                     "additions": 40}],
          "statusCheckRollup": GREEN + [{"name": "triage",
                                         "status": "COMPLETED",
                                         "conclusion": "CANCELLED"}]}
    assert classify([pr], repo="") == {2164: "solid"}
