"""QUEUE 0a per-row half: every C3/C4 Gap Register row names the check that holds it.

Runs tools/row_predicates.py against the real register and predicate file, its
self-test poles, and pins both workflow steps so removing the PR-gate step, making
it continue-on-error, or dropping the nightly escalation is a red required check.
"""
import datetime as dt
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import row_predicates as rp  # noqa: E402


def _steps(workflow):
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", workflow), encoding="utf-8"))
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


def test_self_test():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "row_predicates.py"), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "SELF-TEST PASS" in r.stdout, r.stdout


def test_real_register_every_c3_row_has_a_live_predicate():
    head, preds = rp._load()
    bad, rep = rp.evaluate(head, preds, dt.date.today())
    assert bad == [], bad
    assert len(rep) >= 9


def test_negative_control_dropping_a_predicate_goes_red():
    head, preds = rp._load()
    gid = next(k for k, v in preds["rows"].items() if v["kind"] == "test")
    trimmed = json.loads(json.dumps(preds))
    del trimmed["rows"][gid]
    bad, _ = rp.evaluate(head, trimmed, dt.date.today())
    assert any(v.startswith(f"P1 {gid}") for v in bad), bad


def test_pr_gate_step_is_blocking():
    hit = [s for s in _steps("pr-gates.yml") if "row_predicates.py" in s.get("run", "")]
    assert len(hit) == 1, "row_predicates step missing from pr-gates"
    assert not hit[0].get("continue-on-error"), "row_predicates must block"
    assert "--report" not in hit[0]["run"], "the PR gate must run the rules, not the report"


def test_nightly_escalates_stale_rows():
    steps = _steps("ratchet-nightly.yml")
    assert any("row_predicates.py --report" in s.get("run", "") for s in steps)
    scripts = " ".join(s.get("with", {}).get("script", "") for s in steps)
    assert "chairman: C3+ rows held by stale evidence" in scripts
