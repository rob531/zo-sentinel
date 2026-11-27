"""QUEUE 0a: the gap-claim gate's poles, and that pr-gates still runs it blocking.

Injected-defect acceptance from chairman/QUEUE.md 0a: a synthetic "closed at C1"
PR goes red; a genuine grade move with evidence goes green. The workflow half pins
the step so dropping it, making it continue-on-error, or interpolating the PR body
into the shell is a red required check.
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gap_claim_gate as g  # noqa: E402

REG = open(os.path.join(ROOT, "LOCO_CHAIRMAN.md"), encoding="utf-8").read()


def test_self_test():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gap_claim_gate.py"), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "SELF-TEST PASS" in r.stdout, r.stdout


def test_real_register_parses_every_row_with_a_grade():
    rows = g.rows(REG)
    assert len(rows) >= 25
    assert all(gr is not None for gr, _ in rows.values()), [k for k, (gr, _) in rows.items() if gr is None]


def test_closed_at_c1_on_the_real_register_is_red():
    c1 = next(k for k, (gr, _) in g.rows(REG).items() if gr == 1)
    bad = g.evaluate(f"Closes {c1}", REG, REG)
    assert any(v.startswith(f"R1 {c1}") for v in bad) and any(v.startswith(f"R2 {c1}") for v in bad)


def test_unchanged_register_with_no_claim_is_green():
    assert g.evaluate("build: scaffold something", REG, REG) == []


def _step():
    doc = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "pr-gates.yml"), encoding="utf-8"))
    steps = doc["jobs"]["static-analysis"]["steps"]
    hit = [s for s in steps if "gap_claim_gate.py" in s.get("run", "")]
    assert hit, "gap-claim gate step removed from static-analysis"
    return steps, hit[0]


def test_workflow_runs_the_gate_blocking():
    steps, st = _step()
    assert not st.get("continue-on-error"), "gap-claim gate made non-blocking"
    assert "|| true" not in st["run"] and "--exit-zero" not in st["run"]
    assert steps[0]["with"]["fetch-depth"] == 0, "base register unreadable without full history"


def test_pr_text_is_passed_by_env_not_interpolated():
    _, st = _step()
    assert "github.event.pull_request" not in st["run"]
    assert set(st["env"]) >= {"PR_TITLE", "PR_BODY", "BASE_SHA"}
