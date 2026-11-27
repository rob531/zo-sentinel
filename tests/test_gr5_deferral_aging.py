"""GR-5: the deferral list is aged on cadence and a breach escalates on its own.

Pins three things: the report's poles (self-test), that it reads the real deferral
file, and that ratchet-nightly still runs it and still escalates -- deleting the step
or its issue wiring is the disarm this guards against.
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "deferred_aging_report.py")
NIGHTLY = os.path.join(ROOT, ".github", "workflows", "ratchet-nightly.yml")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import deferred_aging_report as dar  # noqa: E402


def test_self_test_passes():
    r = subprocess.run([sys.executable, TOOL, "--self-test"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "SELF-TEST PASS" in r.stdout, r.stdout + r.stderr


def test_real_deferral_file_is_read_and_every_entry_reported():
    rc, rep = dar.run()
    assert rc == dar.OK, rep
    import reachability_ratchet as rr
    assert rep["totals"]["declared"] == len(rr.load_deferred())
    assert {e["module"] for e in rep["entries"]} == set(rr.load_deferred())


def test_cap_is_the_ratchets_cap():
    import reachability_ratchet as rr
    assert dar.review_cap() == rr.DEFERRED_REVIEW_CAP == 40


def _steps():
    doc = yaml.safe_load(open(NIGHTLY, encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    assert "schedule" in on, "ratchet-nightly lost its cadence"
    return [s for job in doc["jobs"].values() for s in job.get("steps", [])]


def test_nightly_runs_the_aging_report():
    runs = [s.get("run", "") for s in _steps()]
    assert any("tools/deferred_aging_report.py" in r for r in runs)


def test_nightly_escalates_to_an_issue_on_escalate_yes():
    esc = [s for s in _steps() if s.get("name") == "Escalate deferral aging"]
    assert esc, "escalation step removed"
    assert "steps.aging.outputs.escalate == 'yes'" in esc[0]["if"]
    script = esc[0]["with"]["script"]
    assert "issues.create" in script and "issues.update" in script
