"""test_build_routing_m3_trial.py -- the bounded, reversible M3 trial.

BUILDER_M3_TRIAL lets low/high/critical builds pin to the MiniMax-M3 rung
(zo-ladder-medium) instead of the rung-0 M2.7 default, so the failure_matrix
accrues M3 success evidence on those classes. Promotion stays EVIDENCE-driven:
the trial only lets the matrix SEE M3; the deterministic matrix router decides.

Two poles:
  * POLE A (flag unset): BYTE-IDENTICAL to prior behaviour -- low/high/critical
    stay on rung-0 M2.7 (zo-ladder-low). This is the reversibility guarantee.
  * POLE B (flag set): low/high/critical pin to the M3 rung (zo-ladder-medium);
    medium is unchanged (already M3). Flipping the flag off restores pole A.
"""
from __future__ import annotations

from zo_sentinel.build_routing import build_env_for


def _reset(monkeypatch, *, trial=False):
    if trial:
        monkeypatch.setenv("BUILDER_M3_TRIAL", "1")
    else:
        monkeypatch.delenv("BUILDER_M3_TRIAL", raising=False)


# --- POLE A: flag unset => prior behaviour, M2.7 pin intact --------------------

def test_trial_off_is_prior_behaviour(monkeypatch):
    _reset(monkeypatch, trial=False)
    assert build_env_for({"complexity": "low"})["GOOSE_MODEL"] == "zo-ladder-low"
    assert build_env_for({"complexity": "high"})["GOOSE_MODEL"] == "zo-ladder-low"
    assert build_env_for({"complexity": "critical"})["GOOSE_MODEL"] == "zo-ladder-low"
    assert build_env_for({})["GOOSE_MODEL"] == "zo-ladder-low"           # default
    # medium is M3 regardless of the flag
    assert build_env_for({"complexity": "medium"})["GOOSE_MODEL"] == "zo-ladder-medium"


# --- POLE B: flag set => low/high/critical route to the M3 rung ----------------

def test_trial_on_routes_nonmedium_to_m3(monkeypatch):
    _reset(monkeypatch, trial=True)
    assert build_env_for({"complexity": "low"})["GOOSE_MODEL"] == "zo-ladder-medium"
    assert build_env_for({"complexity": "high"})["GOOSE_MODEL"] == "zo-ladder-medium"
    assert build_env_for({"complexity": "critical"})["GOOSE_MODEL"] == "zo-ladder-medium"
    assert build_env_for({})["GOOSE_MODEL"] == "zo-ladder-medium"        # default -> M3
    # medium stays M3
    assert build_env_for({"complexity": "medium"})["GOOSE_MODEL"] == "zo-ladder-medium"


def test_trial_is_reversible(monkeypatch):
    _reset(monkeypatch, trial=True)
    assert build_env_for({"complexity": "high"})["GOOSE_MODEL"] == "zo-ladder-medium"
    _reset(monkeypatch, trial=False)
    assert build_env_for({"complexity": "high"})["GOOSE_MODEL"] == "zo-ladder-low"


def test_trial_does_not_touch_codegen_tier(monkeypatch):
    # The trial only moves GOOSE_MODEL (the orchestrator pin); ZO_BUILD_TIER stays
    # complexity-routed for build_artifact provenance + the delegate fallback.
    _reset(monkeypatch, trial=True)
    env = build_env_for({"complexity": "high"})
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"
    assert env["ZO_BUILD_TIER"] == "zo-ladder-high"
