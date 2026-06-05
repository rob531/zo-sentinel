"""
test_ladder_routing.py -- hermetic tests for complexity-routed ladder start tiers.

Context: every TASK_START_TIER entry used to be 0, and ladder_shim flattened
every request to task_type="builder", so EVERY goose build started (and, since
MiniMax always returns non-empty, ended) at rung 0 -- the 16-rung ladder and its
per-task start-tier capability were dead weight. These tests lock in that a
harder directive (via its model alias) now STARTS at a higher rung, while legacy
callers are unchanged.

Imports only `escalation` (no fastapi/uvicorn), so it runs in the evaluator CI.
Backend adapters are monkeypatched, so no network / API keys are touched.
"""
from __future__ import annotations

import escalation


def test_model_alias_maps_to_task():
    assert escalation.task_for_model("zo-ladder-high") == "builder_high"
    assert escalation.task_for_model("zo-ladder-medium") == "builder_medium"
    assert escalation.task_for_model("zo-ladder-low") == "builder_low"
    assert escalation.task_for_model("zo-ladder-critical") == "builder_critical"
    # back-compat: legacy / unknown ids fall back to rung-0 "builder"
    assert escalation.task_for_model("zo-ladder-v1") == "builder"
    assert escalation.task_for_model("MiniMax-Text-01") == "builder"
    assert escalation.task_for_model(None) == "builder"
    assert escalation.task_for_model("") == "builder"


def test_start_tiers_are_distinct():
    t = escalation.TASK_START_TIER
    assert t["builder_low"] == 0
    assert t["builder_medium"] == 1
    # #80 inserted the MiniMax-M3 rung at index 1, shifting every higher rung +1:
    # builder_high 10->11, builder_critical 14->15.
    assert t["builder_high"] == 11
    assert t["builder_critical"] == 15
    # the legacy default is still rung 0 (unchanged behaviour)
    assert t["builder"] == 0 and t["default"] == 0


def _patch_all_backends(monkeypatch):
    """Make every backend a no-network adapter that records the rung tried and
    succeeds (non-empty text), so ask() stops at the first attempted rung."""
    called = []

    def fake_adapter(spec, prompt, system, max_tokens, temperature, tools):
        called.append(spec.model_id)
        return ("built ok", None, None)

    monkeypatch.setattr(
        escalation, "BACKEND_ADAPTERS",
        {k: fake_adapter for k in escalation.BACKEND_ADAPTERS},
    )
    return called


def test_ask_starts_at_mapped_rung(monkeypatch):
    called = _patch_all_backends(monkeypatch)

    r = escalation.ask("builder_high", "build a complex thing")
    assert r.success
    assert called[0] == escalation.LADDER[11].model_id  # started at rung 11 (#80 shift), not 0

    called.clear()
    r2 = escalation.ask("builder_low", "build a trivial thing")
    assert r2.success
    assert called[0] == escalation.LADDER[0].model_id    # low still starts at MiniMax


def _patch_all_backends_fail(monkeypatch):
    """Every backend returns empty (a failure) so ask() cascades through every
    ELIGIBLE rung -- records which model_ids were actually attempted."""
    called = []

    def fail_adapter(spec, prompt, system, max_tokens, temperature, tools):
        called.append(spec.model_id)
        return (None, "forced-fail", None)

    monkeypatch.setattr(
        escalation, "BACKEND_ADAPTERS",
        {k: fail_adapter for k in escalation.BACKEND_ADAPTERS},
    )
    return called


_PAID_MODELS = {s.model_id for s in escalation.LADDER if s.cost_priority > 0}


def test_paid_rungs_cost_gated_for_non_critical(monkeypatch):
    # a medium build whose free rungs all fail must NOT cascade into paid rungs
    called = _patch_all_backends_fail(monkeypatch)
    r = escalation.ask("builder_medium", "x", max_attempts=16)  # full ladder
    assert not r.success
    assert not (set(called) & _PAID_MODELS), \
        f"non-critical task hit paid rung(s): {set(called) & _PAID_MODELS}"


def test_critical_may_reach_paid_rungs(monkeypatch):
    called = _patch_all_backends_fail(monkeypatch)
    escalation.ask("builder_critical", "x", max_attempts=16)
    assert set(called) & _PAID_MODELS, "builder_critical never tried a paid rung"


def test_medium_starts_at_minimax_m3(monkeypatch):
    called = _patch_all_backends(monkeypatch)
    r = escalation.ask("builder_medium", "moderate build")
    assert r.success
    # #80: rung 1 is MiniMax-M3 (the medium-pinned rung), NOT rung-0 MiniMax-M2.7.
    assert called[0] == escalation.LADDER[1].model_id    # rung 1 = MiniMax-M3
    assert called[0] != escalation.LADDER[0].model_id    # explicitly NOT rung-0 M2.7
