"""The refresh half of the delta cohort went to ZERO and nothing read the number.

MEASURED 2026-09-01 from the live run states in D:\\zo\\runs\\weekly_rescore, the
`overall_risk` axis of each landed delta wave's own `delta_summary`:

    run              refresh_servers   changed        rate
    20260726-014732           20,000    15,236     76.2 %
    20260727-024623          120,000    97,989     81.6 %
    20260727-105859          140,000   136,116     97.2 %
    20260730-001738           20,000         0      0.0 %   <-- regime change
    20260804-060703           20,000         0      0.0 %
    20260831-033413           20,000         1      0.005 %

Three consecutive landed waves spent 60,000 server-slots -- the majority of every
delta cohort -- to move ONE server on ONE axis. The harness recorded all of it,
into `state.json` and into the `score_change_runs` table, and no instrument ever
read it back, so the phase was invisible for four weeks and roughly a dollar of
GPU time. This is the fleet's own recurring shape: the number existed, was
correct, and was never the input to anything.

WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT
`refresh_yield()` is a REPORT, not a gate. It does not abort, does not change an
exit code and does not veto a wave -- HARNESS_DOCTRINE R7 (prefer RECOVERY over
RESTRICTION), and the standing instruction not to answer a finding by proposing
another required check. Deciding what to DO about a zero-yield refresh half
(shrinking `--refresh-cap`, reallocating the budget to never-scored servers) is
cohort policy and belongs to peer review, not to a unilateral edit here.

R6 IS THE POINT OF HALF THESE CASES. A run whose `delta_summary` is absent --
capture disabled, an import that died, a full-mode run -- must report
`unmeasured`, never `0`. A refresh phase we did not measure and a refresh phase
that changed nothing are the same value to a naive reader and opposite facts to
a decision, and collapsing them is how "unknown" gets published as "zero".
"""
from __future__ import annotations

import importlib.util
import sys
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


def _axes(**changed_by_axis):
    """delta_summary in the shape ph_import actually writes it."""
    return {
        axis: {"new": 12689, "changed": n, "unchanged": 20000 - n}
        for axis, n in changed_by_axis.items()
    }


# --------------------------------------------------------------- the defect

def test_the_20260831_shape_is_zero_yield(wr):
    """1 change in 20,000 refresh servers. Verbatim from run 20260831-033413."""
    y = wr.refresh_yield({
        "mode": "delta",
        "refresh_servers": 20000,
        "delta_summary": _axes(overall_risk=1, auth_strength=0, capability_breadth=0,
                               data_sensitivity=0, network_egress=0,
                               maintainer_trust=0, exploit_surface=0),
    })
    assert y["verdict"] == "zero_yield"
    assert y["changed"] == 1
    assert y["refresh_servers"] == 20000
    assert y["rate"] == pytest.approx(1 / 20000)
    # R5: a number without its basis is how "MTD spend" became a 24h delta.
    assert "delta_summary" in y["basis"]


def test_the_20260804_shape_is_zero_yield(wr):
    """0 in 20,000 -- the first wave after the regime change, unremarked at the time."""
    y = wr.refresh_yield({
        "mode": "delta", "refresh_servers": 20000,
        "delta_summary": _axes(overall_risk=0, auth_strength=0, exploit_surface=0),
    })
    assert y["verdict"] == "zero_yield"
    assert y["changed"] == 0
    assert y["rate"] == 0.0


def test_changed_is_the_max_across_axes_not_just_overall_risk(wr):
    """A wave that moved only `auth_strength` is productive, and a summary keyed
    solely on `overall_risk` would call it dead. Seven axes ship; read seven."""
    y = wr.refresh_yield({
        "mode": "delta", "refresh_servers": 20000,
        "delta_summary": _axes(overall_risk=0, auth_strength=9000, exploit_surface=3),
    })
    assert y["changed"] == 9000
    assert y["verdict"] == "productive"


# ------------------------------------------- the negative control (R4)

def test_the_20260727_shape_is_productive(wr):
    """97.2% of 140,000. If this ever reads zero_yield the threshold is broken,
    and the report would be condemning the healthiest wave on record."""
    y = wr.refresh_yield({
        "mode": "delta", "refresh_servers": 140000,
        "delta_summary": {"overall_risk": {"new": 5, "changed": 136116,
                                           "unchanged": 3884}},
    })
    assert y["verdict"] == "productive"
    assert y["rate"] == pytest.approx(136116 / 140000)


def test_just_above_the_threshold_is_not_zero_yield(wr):
    """The bar is a bar, not a mood. 0.1% of 20,000 is 20 servers."""
    assert wr.refresh_yield({
        "mode": "delta", "refresh_servers": 20000,
        "delta_summary": _axes(overall_risk=25),
    })["verdict"] != "zero_yield"
    assert wr.refresh_yield({
        "mode": "delta", "refresh_servers": 20000,
        "delta_summary": _axes(overall_risk=19),
    })["verdict"] == "zero_yield"


# --------------------------------------------------------------- R6 cases

def test_absent_delta_summary_is_unmeasured_not_zero(wr):
    """UNKNOWN IS NOT ZERO. A run with capture off, or one whose import died
    before the aggregates were written, has told us nothing about its refresh
    half -- and `0` is the single most misleading thing we could publish."""
    y = wr.refresh_yield({"mode": "delta", "refresh_servers": 20000,
                          "delta_summary": None})
    assert y["verdict"] == "unmeasured"
    assert y["changed"] is None
    assert y["rate"] is None


def test_empty_delta_summary_is_unmeasured_not_zero(wr):
    y = wr.refresh_yield({"mode": "delta", "refresh_servers": 20000,
                          "delta_summary": {}})
    assert y["verdict"] == "unmeasured"
    assert y["rate"] is None


def test_no_refresh_half_reports_nothing(wr):
    """A cohort with no refresh servers has no refresh yield to describe.
    Silence here, so the loud line means exactly one thing when it appears."""
    assert wr.refresh_yield({"mode": "delta", "refresh_servers": 0,
                             "delta_summary": _axes(overall_risk=0)}) is None
    assert wr.refresh_yield({"mode": "delta",
                             "delta_summary": _axes(overall_risk=0)}) is None


def test_a_died_run_is_unmeasured(wr):
    """20260822-220319 died at deadline: refresh_servers stamped by export,
    nothing else. It must not be counted as a zero-yield refresh phase --
    it never ran one."""
    y = wr.refresh_yield({"mode": "delta", "refresh_servers": 20000,
                          "result": "deadline"})
    assert y["verdict"] == "unmeasured"


# ------------------------------------------------- it reaches the artifact

def test_postcheck_report_carries_the_yield(wr, tmp_path, monkeypatch):
    """A finding that stops at a log line is a finding nobody reads next week.
    It has to land in report.json, which is the artifact a successor opens."""
    monkeypatch.setattr(wr, "freshness_safe", lambda: ({}, "probe: not called out"))
    monkeypatch.setattr(wr, "ledger", lambda *a, **k: None)

    run_dir = tmp_path / "20260901-000000"
    run_dir.mkdir()
    run = wr.Run(run_dir)
    run.state.update({
        "run_id": "20260901-000000", "mode": "delta",
        "phases": {}, "baseline_freshness": {},
        "exported": 32689, "imported_servers": 32689, "coverage": 1.0,
        "degraded": False, "est_cost": 0.4, "scored_after": 296109,
        "refresh_servers": 20000, "new_servers": 12689,
        "delta_summary": _axes(overall_risk=1),
    })

    wr.ph_postcheck(run, None)

    report = run.state["report"]
    assert report["refresh_yield"]["verdict"] == "zero_yield"
    assert report["refresh_yield"]["changed"] == 1
    on_disk = (run_dir / "report.json").read_text(encoding="utf-8")
    assert "refresh_yield" in on_disk
