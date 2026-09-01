"""The cost ceiling must be compared against the rate we are BILLED.

`ph_fire` stamps state["dph"] from the vast OFFER's dph_total. That prices
compute only; once the instance is rented, vast adds the allocated-storage
component, so the live instance's dph_total is strictly >= the offer's.

Observed on the live campaign wave 20260727-105859:
    state.json dph        = 0.2961111111111111   (offer, what we watched against)
    live instance dph_tot = 0.3211111111111111   (what the invoice charges)
an 8.4% under-count. Every "watch: ... est $x" line understated the bill, and
COST_CAP_USD -- the guard that exists to stop a runaway paid job -- fired ~8%
late. Same failure shape as FU-035, where the MTD spend guard compared against a
number that was never real: a ceiling is only as honest as its basis.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Run:
    def __init__(self, **state):
        self.state = state


class _Args:
    max_dph = 0.45


def _stub_sdk(monkeypatch, wr, instances):
    """Stand in for vastai_sdk without importing it."""
    import sys, types

    class _V:
        def __init__(self, api_key=None):
            pass

        def show_instances(self):
            return instances

    mod = types.ModuleType("vastai_sdk")
    mod.VastAI = _V
    monkeypatch.setitem(sys.modules, "vastai_sdk", mod)
    monkeypatch.setattr(wr, "secret", lambda name: "x")
    monkeypatch.setattr(wr, "log", lambda *a, **k: None)


def test_live_wave_20260727_the_exact_undercount(wr, monkeypatch):
    """The real numbers off the live instance, not a synthetic pair."""
    _stub_sdk(monkeypatch, wr, [
        {"id": 45996047, "dph_total": 0.3211111111111111, "actual_status": "running"},
    ])
    run = _Run(dph=0.2961111111111111, instance_id=45996047)
    got = wr._billed_dph(run, _Args())
    assert got == pytest.approx(0.3211111111111111)
    # and the ceiling now trips on time rather than 8% late
    assert got > 0.2961111111111111


def test_other_instances_are_not_confused_for_ours(wr, monkeypatch):
    _stub_sdk(monkeypatch, wr, [
        {"id": 111, "dph_total": 9.99},
        {"id": 45996047, "dph_total": 0.33},
    ])
    run = _Run(dph=0.30, instance_id=45996047)
    assert wr._billed_dph(run, _Args()) == pytest.approx(0.33)


def test_a_lower_live_rate_never_lowers_the_basis(wr, monkeypatch):
    """A ceiling must not be relaxed by a surprising API answer."""
    _stub_sdk(monkeypatch, wr, [{"id": 7, "dph_total": 0.10}])
    run = _Run(dph=0.30, instance_id=7)
    assert wr._billed_dph(run, _Args()) == pytest.approx(0.30)


def test_api_failure_falls_back_and_does_not_kill_the_watch(wr, monkeypatch):
    """FU-112 scar: a watch loop must never die because a read failed."""
    import sys, types

    class _V:
        def __init__(self, api_key=None):
            raise RuntimeError("vast is having a moment")

    mod = types.ModuleType("vastai_sdk")
    mod.VastAI = _V
    monkeypatch.setitem(sys.modules, "vastai_sdk", mod)
    monkeypatch.setattr(wr, "secret", lambda name: "x")
    monkeypatch.setattr(wr, "log", lambda *a, **k: None)
    run = _Run(dph=0.30, instance_id=7)
    assert wr._billed_dph(run, _Args()) == pytest.approx(0.30)


def test_missing_instance_id_uses_quoted(wr, monkeypatch):
    monkeypatch.setattr(wr, "log", lambda *a, **k: None)
    assert wr._billed_dph(_Run(dph=0.28), _Args()) == pytest.approx(0.28)


def test_instance_absent_from_listing_uses_quoted(wr, monkeypatch):
    _stub_sdk(monkeypatch, wr, [])
    assert wr._billed_dph(_Run(dph=0.28, instance_id=42), _Args()) == pytest.approx(0.28)


def test_watch_loop_actually_calls_it(wr):
    """A helper nobody calls is a placebo (the UNCALLED-gate scar)."""
    import inspect
    src = inspect.getsource(wr.ph_watch_collect)
    assert "_billed_dph(run, args)" in src
    assert 'run.state.get("dph"' not in src
