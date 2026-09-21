"""The watch line must publish the status the loop already holds.

Wave 20260909-014759 printed forty minutes of

    watch: no results yet (elapsed 0.64h, est $0.20)

while `state.json` recorded `status_seen == ["unknown", "loading", "running"]`
and the pod was ~30% through its 28,601-row cohort. Nothing was broken. But the
only human-readable surface could not distinguish "the machine is wedged and
this money is being burned for nothing" from "the job is running and slow" --
so an operator had to hand-roll a script against the vast API to learn a fact
the loop had probed, stored and thrown away one line earlier.

That is the fleet's most expensive recurring shape (FU-358: refresh yield was
written to state.json and to a table for four weeks and was the input to
nothing), and R5: publish the BASIS with every number.

Report-only. No gate, no new required check, no exit code changes (R7).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weekly_rescore"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Run:
    def __init__(self, **state):
        self.state = state
        self.saves = 0

    def save(self):
        self.saves += 1


def _no_pod(wr, monkeypatch):
    """Silence the live vast call; _pod_progress is exercised separately."""
    monkeypatch.setattr(wr, "_pod_progress", lambda run: "")


def test_running_status_reaches_the_line(wr, monkeypatch):
    _no_pod(wr, monkeypatch)
    run = _Run(instance_id=50335633)
    basis = wr._watch_basis(run, {"present": True, "actual_status": "running"})
    assert "running" in basis


def test_loading_is_distinguishable_from_running(wr, monkeypatch):
    """The negative control. If both render the same, the line is useless --
    telling a wedge from a slow job is the entire point."""
    _no_pod(wr, monkeypatch)
    run = _Run(instance_id=1)
    loading = wr._watch_basis(run, {"present": True, "actual_status": "loading"})
    running = wr._watch_basis(run, {"present": True, "actual_status": "running"})
    assert loading != running
    assert "loading" in loading


def test_unreadable_probe_says_unknown_not_a_status(wr, monkeypatch):
    """R6: an unreadable probe is UNKNOWN. It must never render as a status,
    and above all never as the reassuring one."""
    _no_pod(wr, monkeypatch)
    basis = wr._watch_basis(_Run(instance_id=1), {})
    assert "UNKNOWN" in basis
    assert "running" not in basis


def test_absent_instance_is_named_absent(wr, monkeypatch):
    _no_pod(wr, monkeypatch)
    basis = wr._watch_basis(_Run(instance_id=1), {"present": False})
    assert "absent" in basis


def test_pod_progress_is_appended_when_available(wr, monkeypatch):
    monkeypatch.setattr(wr, "_pod_progress", lambda run: "8600/28601")
    basis = wr._watch_basis(_Run(instance_id=1), {"present": True, "actual_status": "running"})
    assert "8600/28601" in basis


def test_pod_progress_is_omitted_when_empty(wr, monkeypatch):
    """Absence of progress must print nothing, never 'pod 0/0'. A fabricated
    zero is worse than a gap: it reads as a measurement."""
    monkeypatch.setattr(wr, "_pod_progress", lambda run: "")
    basis = wr._watch_basis(_Run(instance_id=1), {"present": True, "actual_status": "running"})
    assert "pod" not in basis


def test_pod_progress_picks_the_cohort_bar_not_the_shard_loader(wr, monkeypatch):
    """The pod log carries several tqdm bars. `Loading checkpoint shards: 2/2`
    is not cohort progress; `inputs.jsonl: 8600/28601 [...]` is."""
    log = (
        "Loading checkpoint shards: 100%|##| 2/2 [00:02<00:00,  1.15s/it]\n"
        "Fetching 12 files: 100%|##| 12/12 [07:57<00:00, 39.77s/it]\n"
        "inputs.jsonl:  30%|###   | 8600/28601 [09:12<21:44,  7.14it/s]\n"
    )
    monkeypatch.setattr(wr, "secret", lambda name: "k")
    fake = type("V", (), {"logs": lambda self, *a, **k: log})
    monkeypatch.setitem(sys.modules, "vastai_sdk",
                        types.SimpleNamespace(VastAI=lambda **k: fake()))
    run = _Run(instance_id=1)
    assert wr._pod_progress(run) == "8600/28601"


def test_pod_progress_returns_empty_when_the_api_raises(wr, monkeypatch):
    """Never raise out of a report. A forensics helper that can kill the watch
    loop is a strictly worse instrument than no helper at all."""
    def boom(**kwargs):
        raise RuntimeError("vast is down")

    monkeypatch.setattr(wr, "secret", lambda name: "k")
    monkeypatch.setitem(sys.modules, "vastai_sdk", types.SimpleNamespace(VastAI=boom))
    assert wr._pod_progress(_Run(instance_id=1)) == ""


def test_pod_progress_is_cached_between_polls(wr, monkeypatch):
    """The watch loop polls every ~120s; the logs API must not be hit each time."""
    calls = []

    def logs(self, *a, **k):
        calls.append(1)
        return "inputs.jsonl: 1/28601 [00:00<2:00:56,  3.94it/s]\n"

    monkeypatch.setattr(wr, "secret", lambda name: "k")
    fake = type("V", (), {"logs": logs})
    monkeypatch.setitem(sys.modules, "vastai_sdk",
                        types.SimpleNamespace(VastAI=lambda **k: fake()))
    run = _Run(instance_id=1)
    wr._pod_progress(run)
    wr._pod_progress(run)
    assert len(calls) == 1


def test_no_instance_id_is_not_an_error(wr):
    assert wr._pod_progress(_Run()) == ""


def _fake_logs(monkeypatch, wr, texts):
    """Serve `texts` in order, repeating the last one forever."""
    seq = list(texts)

    def logs(self, *a, **k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(wr, "secret", lambda name: "k")
    fake = type("V", (), {"logs": logs})
    monkeypatch.setitem(sys.modules, "vastai_sdk",
                        types.SimpleNamespace(VastAI=lambda **k: fake()))


def test_frozen_log_snapshot_publishes_nothing(wr, monkeypatch):
    """MEASURED on instance 50335633: the vast logs API served byte-identical
    text across 5 calls and 12 minutes while the pod was demonstrably scoring.
    Republishing `7/28601` every poll would be a carried value wearing the
    costume of a measured one. Second identical fetch must yield "".
    """
    frozen = "inputs.jsonl:   0%|  | 7/28601 [00:01<2:00:56,  3.94it/s]\n"
    _fake_logs(monkeypatch, wr, [frozen])
    run = _Run(instance_id=1)
    assert wr._pod_progress(run) == "7/28601"        # first sight: honest
    run.state["_pod_progress_at"] = 0                # expire the cache
    assert wr._pod_progress(run) == ""               # unchanged: say nothing


def test_moving_log_keeps_publishing(wr, monkeypatch):
    """The negative control for the guard above. If a CHANGING log were also
    suppressed, the guard would have bought honesty by going blind."""
    _fake_logs(monkeypatch, wr, [
        "inputs.jsonl: 7/28601 [00:01<2:00:56,  3.94it/s]\n",
        "inputs.jsonl: 9000/28601 [09:12<21:44,  7.14it/s]\n",
    ])
    run = _Run(instance_id=1)
    assert wr._pod_progress(run) == "7/28601"
    run.state["_pod_progress_at"] = 0
    assert wr._pod_progress(run) == "9000/28601"
