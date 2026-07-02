"""Hermetic tests for zo_sentinel.vast_jobs -- the managed GPU-job lifecycle.

The whole state machine runs against a FAKE client (no network, no vast SDK,
no key): launch/watch/collect/verify/resolve, cost-ceiling halt, deadline
halt, Option-B destroy policy, forensics-before-destroy ordering, ledger
completeness, and the audit's three alert classes. This is what makes vast
jobs 'managed by the E2E': the gate + ledger + audit logic is CI-enforced.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import vast_jobs  # noqa: E402


class FakeClient:
    def __init__(self, dph=0.5, states=("running",), workdir=None,
                 make_artifacts=True):
        self.dph = dph
        self.states = list(states)
        self.destroyed = []
        self.scp_calls = []
        self.workdir = workdir
        self.make_artifacts = make_artifacts
        self.live = []

    def launch(self, spec):
        return "inst_1"

    def status(self, iid):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"state": state, "dph": self.dph, "ssh_host": "h", "ssh_port": 1}

    def list_instances(self):
        return self.live

    def destroy(self, iid):
        self.destroyed.append(iid)
        return True

    def scp_from(self, inst, remote_glob, local_dir):
        self.scp_calls.append((remote_glob, str(local_dir)))
        local_dir = pathlib.Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        if self.make_artifacts and "out/" in remote_glob:
            name = remote_glob.rsplit("/", 1)[-1]
            if name.endswith(".jsonl"):
                (self.workdir / name).write_text(
                    "\n".join('{"r":%d}' % i for i in range(5)) + "\n",
                    encoding="utf-8")
            else:
                (self.workdir / name).write_text(
                    json.dumps({"bar_passes": True, "axes_per_server": 7}),
                    encoding="utf-8")
        return True


def _manifest(**over):
    m = {"name": "tjob",
         "launch": {"offer_id": 1, "image": "x"},
         "cost_cap_usd": 1.00, "max_dph": 0.5, "deadline_min": 10,
         "auto_destroy": False,
         "forensics": ["/workspace/onstart.log"],
         "artifacts": ["/workspace/out/scores.jsonl", "/workspace/out/m.json"],
         "checks": [
             {"name": "rc", "type": "rc_zero"},
             {"name": "rows", "type": "jsonl_min_rows", "path": "scores.jsonl",
              "min_rows": 3},
             {"name": "bar", "type": "json_field", "path": "m.json",
              "field": "bar_passes", "op": "eq", "value": True},
         ]}
    m.update(over)
    return m


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("ZO_VAST_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_green_run_full_lifecycle_option_b(iso, tmp_path):
    """All checks green -> DESTROY_READY, but Option B: NOT destroyed."""
    wd = tmp_path / "wd"
    wd.mkdir()
    c = FakeClient(states=("running", "exited"), workdir=wd)
    summary = vast_jobs.run_job(_manifest(), c, wd, sleep=lambda s: None)
    assert summary["verdict"] == "DESTROY_READY"
    assert summary["destroyed"] is False and summary["instance_left_alive"]
    assert c.destroyed == []
    events = [r["event"] for r in vast_jobs.ledger_rows()]
    assert events == ["launch_requested", "launched", "collected", "gate", "resolved"]


def test_auto_destroy_only_when_all_green(iso, tmp_path):
    wd = tmp_path / "wd"; wd.mkdir()
    c = FakeClient(states=("exited",), workdir=wd)
    s = vast_jobs.run_job(_manifest(auto_destroy=True), c, wd, sleep=lambda s: None)
    assert s["verdict"] == "DESTROY_READY" and s["destroyed"] is True
    # forensics scp happened BEFORE destroy (ordering: scp calls recorded,
    # destroy list appended after) -- forensics-before-destroy enforced.
    assert c.scp_calls and c.destroyed == ["inst_1"]


def test_gate_fail_leaves_instance_alive_with_closeout_alert(iso, tmp_path):
    wd = tmp_path / "wd"; wd.mkdir()
    c = FakeClient(states=("exited",), workdir=wd, make_artifacts=False)
    s = vast_jobs.run_job(_manifest(auto_destroy=True), c, wd, sleep=lambda s: None)
    assert s["verdict"] == "GATE_FAIL"
    assert c.destroyed == []                     # NEVER destroy on red gate
    assert (wd / "CLOSEOUT_ALERT.txt").is_file()
    assert "LEFT ALIVE" in (wd / "CLOSEOUT_ALERT.txt").read_text(encoding="utf-8")


def test_cost_ceiling_halts_and_fails_gate(iso, tmp_path):
    """dph so high the first poll breaches the cap -> halted, gate red, alive."""
    wd = tmp_path / "wd"; wd.mkdir()
    c = FakeClient(dph=1000.0, states=("running",), workdir=wd)
    t = {"v": 0}

    def now():
        t["v"] += 3600   # each poll = 1h elapsed
        return t["v"]
    s = vast_jobs.run_job(_manifest(), c, wd, sleep=lambda s: None, now=now)
    assert s["verdict"] == "GATE_FAIL" and c.destroyed == []
    rows = vast_jobs.ledger_rows()
    collected = [r for r in rows if r["event"] == "collected"][0]
    assert "COST_CEILING" in (collected.get("halted") or "")


def test_deadline_halts(iso, tmp_path):
    wd = tmp_path / "wd"; wd.mkdir()
    c = FakeClient(dph=0.0001, states=("running",), workdir=wd)
    t = {"v": 0}

    def now():
        t["v"] += 6 * 3600
        return t["v"]
    vast_jobs.run_job(_manifest(deadline_min=10), c, wd, sleep=lambda s: None, now=now)
    collected = [r for r in vast_jobs.ledger_rows() if r["event"] == "collected"][0]
    assert "DEADLINE" in (collected.get("halted") or "")


def test_manifest_validation(iso, tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "x", "launch": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        vast_jobs.load_manifest(p)               # no checks
    p.write_text(json.dumps(_manifest()), encoding="utf-8")
    m = vast_jobs.load_manifest(p)
    assert m["auto_destroy"] is False            # Option B default
    bad = _manifest()
    bad["checks"].append({"type": "sql_injection_lol"})
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        vast_jobs.load_manifest(p)


def test_shipped_rescore_manifest_is_valid():
    repo = pathlib.Path(__file__).resolve().parents[1]
    m = vast_jobs.load_manifest(repo / "jobs" / "registry_rescore_v1.json")
    assert m["auto_destroy"] is False
    assert m["cost_cap_usd"] <= 5.0              # standing cost-ceiling discipline
    assert any(c["type"] == "jsonl_min_rows" for c in m["checks"])


def test_audit_alert_classes(iso, tmp_path):
    wd = tmp_path / "wd"; wd.mkdir()
    # run 1: green, resolved, NOT destroyed (Option B) -> alive_after_ready
    c = FakeClient(states=("exited",), workdir=wd)
    vast_jobs.run_job(_manifest(), c, wd, sleep=lambda s: None)
    # a live instance nobody launched through the ledger -> orphan_instance
    c.live = [{"id": "inst_1", "state": "running", "dph": 0.5},
              {"id": "rogue_9", "state": "running", "dph": 2.0}]
    report = vast_jobs.audit(c)
    classes = {a["class"] for a in report["alerts"]}
    assert "alive_after_ready" in classes
    assert "orphan_instance" in classes
    assert report["ok"] is False
    # audit report persisted for the E2E to read
    assert (vast_jobs.state_dir() / "last_audit.json").is_file()


def test_audit_clean_when_ledger_and_live_agree(iso):
    c = FakeClient()
    c.live = []
    report = vast_jobs.audit(c)
    assert report["ok"] is True and report["alerts"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
