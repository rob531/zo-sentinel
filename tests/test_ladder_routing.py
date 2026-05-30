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
    assert t["builder_high"] == 10
    assert t["builder_critical"] == 14
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
    assert called[0] == escalation.LADDER[10].model_id  # started at rung 10, not 0

    called.clear()
    r2 = escalation.ask("builder_low", "build a trivial thing")
    assert r2.success
    assert called[0] == escalation.LADDER[0].model_id    # low still starts at MiniMax


def test_medium_starts_at_gemini(monkeypatch):
    called = _patch_all_backends(monkeypatch)
    r = escalation.ask("builder_medium", "moderate build")
    assert r.success
    assert called[0] == escalation.LADDER[1].model_id    # rung 1 = first Gemini
    assert called[0] != escalation.LADDER[0].model_id    # explicitly NOT MiniMax
