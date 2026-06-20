"""ask() cross-model failover: a 429'd rung parks and we fall over to a DIFFERENT
free model instead of dead-ending. Backoff disabled so the test is fast."""
import os
import pytest
import escalation as E
import rung_quota as Q


def test_failover_parks_and_switches_model(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "QUOTA_FILE", tmp_path / "q.json")
    monkeypatch.setenv("LADDER_BACKOFF", "0")
    monkeypatch.setenv("LADDER_FAILOVER_EXTRA", "50")

    def fail_429(spec, *a, **k):
        return None, "429 rate-limited", None

    def ok_oai(spec, *a, **k):
        return "<!doctype html>ok", None, None

    # every backend 429s EXCEPT the openai_compatible capacity rungs
    monkeypatch.setattr(E, "BACKEND_ADAPTERS", {
        "minimax_direct": fail_429, "gemini_direct": fail_429,
        "zo_routed": fail_429, "openai_compatible": ok_oai})

    r = E.ask("builder_low", "build something", max_tokens=64)
    assert r.success is True
    assert r.backend == "openai_compatible"          # failed over to a different model
    # at least one rate-limited rung got parked
    snap = Q.snapshot()
    assert any(e.get("parked_now") for e in snap.values())


def test_preparked_rung_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "QUOTA_FILE", tmp_path / "q.json")
    monkeypatch.setenv("LADDER_BACKOFF", "0")
    # park the rung-0 model up front
    m0 = E.LADDER[0].model_id
    Q.park(m0, seconds=300)
    captured = {}

    def ok_any(spec, *a, **k):
        captured["model"] = spec.model_id
        return "ok", None, None
    monkeypatch.setattr(E, "BACKEND_ADAPTERS", {b: ok_any for b in E.BACKEND_ADAPTERS})

    r = E.ask("builder_low", "x", max_tokens=16)
    assert r.success and r.model != m0               # skipped the parked rung
    assert any(a[1] == "quota_skip" for a in r.attempts)
