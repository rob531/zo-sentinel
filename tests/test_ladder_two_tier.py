"""test_ladder_two_tier.py -- the free floor + advanced ceiling two-tier routing.

Locks in the two-tier inference change:
  * FLOOR  = tower RTX 3060 (local Ollama, hermes3:8b) -- a FREE rung, GATED OFF
    by TOWER_OLLAMA_ENABLED (default unset) because the endpoint is firewall-
    blocked until a separate admin step. With the flag unset the rung is pruned
    and start-tiers are IDENTICAL to today (fail-graceful proof).
  * CEILING = BEAM/Reflection via OpenRouter (OPENROUTER_API_KEY) -- a capable
    cloud synthesizer ranked ABOVE the floor; hard synthesis task types START
    here when the key is present. Existing frontier rungs are KEPT.

Imports only `escalation` (no fastapi/uvicorn / network / keys). Env is
monkeypatched + apply_two_tier_routing() re-run, so the module-level import-time
state is never relied on.
"""
from __future__ import annotations

import escalation


# The task types whose start-tier the two-tier routing may rewrite, and today's
# baseline values -- the invariant the fail-graceful proof rests on.
_BASELINE = {
    "builder_low": 0, "generate": 0, "signal_enrichment": 0, "classification": 0,
    "builder_high": 11, "builder_critical": 15, "wisdom_synthesis": 0,
}


def _reset(monkeypatch, *, floor=False, ceiling=False):
    if floor:
        monkeypatch.setenv("TOWER_OLLAMA_ENABLED", "1")
    else:
        monkeypatch.delenv("TOWER_OLLAMA_ENABLED", raising=False)
    if ceiling:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy")
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    escalation.apply_two_tier_routing()


# --- the two new rungs exist, appended last, correctly configured -------------

def test_floor_rung_present_and_configured():
    sp = escalation.LADDER[escalation._FLOOR_RUNG]
    assert sp.backend == "openai_compatible"
    assert sp.model_id == "hermes3:8b"
    assert sp.cost_priority == 0.0                      # cheapest rung
    assert sp.key_env == "TOWER_OLLAMA_API_KEY"
    assert sp.extra_params.get("num_ctx") == 8192
    assert "11434" in sp.base_url                       # tower Ollama endpoint


def test_ceiling_rung_present_and_keyed():
    sp = escalation.LADDER[escalation._CEILING_RUNG]
    assert sp.backend == "openai_compatible"
    assert sp.model_id == "reflection/Beam-1.0-501B-A23B"
    assert sp.key_env == "OPENROUTER_API_KEY"
    assert sp.base_url == "https://openrouter.ai/api/v1"
    assert sp.cost_priority > 0                         # a paid ceiling rung


def test_floor_is_ranked_below_ceiling():
    # cheapest floor sits BELOW the capable ceiling in cost and above it in the
    # ladder-order sense (floor = low-complexity default, ceiling = hard start).
    floor = escalation.LADDER[escalation._FLOOR_RUNG]
    ceiling = escalation.LADDER[escalation._CEILING_RUNG]
    assert floor.cost_priority < ceiling.cost_priority
    assert floor.model_id == "hermes3:8b"


# --- FAIL-GRACEFUL: flag unset => identical to today --------------------------

def test_floor_disabled_and_baseline_when_flag_unset(monkeypatch):
    _reset(monkeypatch, floor=False, ceiling=False)
    assert escalation.LADDER[escalation._FLOOR_RUNG].enabled is False
    for t, base in _BASELINE.items():
        assert escalation.TASK_START_TIER[t] == base, t
    assert escalation.PAID_OK_TASKS == {"builder_critical"}


def test_routing_reversible(monkeypatch):
    # turn both tiers ON, then OFF, and prove the baseline is fully restored
    _reset(monkeypatch, floor=True, ceiling=True)
    assert escalation.LADDER[escalation._FLOOR_RUNG].enabled is True
    _reset(monkeypatch, floor=False, ceiling=False)
    assert escalation.LADDER[escalation._FLOOR_RUNG].enabled is False
    for t, base in _BASELINE.items():
        assert escalation.TASK_START_TIER[t] == base, t
    assert escalation.PAID_OK_TASKS == {"builder_critical"}


# --- FLOOR enabled: routine/low-complexity work starts on the free floor ------

def test_floor_routes_routine_work_when_enabled(monkeypatch):
    _reset(monkeypatch, floor=True, ceiling=False)
    assert escalation.LADDER[escalation._FLOOR_RUNG].enabled is True
    for t in ("builder_low", "generate", "signal_enrichment", "classification"):
        assert escalation.TASK_START_TIER[t] == escalation._FLOOR_RUNG, t
    # hard tiers untouched when only the floor is on
    assert escalation.TASK_START_TIER["builder_high"] == 11


# --- CEILING keyed: hard synthesis starts at/above the ceiling ----------------

def test_ceiling_routes_hard_work_when_keyed(monkeypatch):
    _reset(monkeypatch, floor=False, ceiling=True)
    for t in ("builder_high", "builder_critical", "wisdom_synthesis"):
        assert escalation.TASK_START_TIER[t] == escalation._CEILING_RUNG, t
    # and those tasks are allowed to spend on the (paid) ceiling rung
    assert {"builder_high", "wisdom_synthesis"} <= escalation.PAID_OK_TASKS
    # routine tiers untouched when only the ceiling is on
    assert escalation.TASK_START_TIER["builder_low"] == 0


def test_ask_starts_hard_work_at_ceiling_when_keyed(monkeypatch):
    """End-to-end through ask(): a keyed ceiling makes builder_high START on the
    BEAM/Reflection rung (adapters stubbed -> no network)."""
    _reset(monkeypatch, floor=False, ceiling=True)
    called = []

    def fake_adapter(spec, prompt, system, max_tokens, temperature, tools):
        called.append(spec.model_id)
        return ("built ok", None, None)

    monkeypatch.setattr(escalation, "BACKEND_ADAPTERS",
                        {k: fake_adapter for k in escalation.BACKEND_ADAPTERS})
    r = escalation.ask("builder_high", "hard synthesis", max_attempts=4)
    assert r.success
    assert called[0] == escalation.LADDER[escalation._CEILING_RUNG].model_id
    # cleanup: restore baseline so later tests are unaffected
    _reset(monkeypatch, floor=False, ceiling=False)


# --- PROXY: tower floor goes through the SOCKS proxy; cloud rungs go direct ----
#
# zocompute runs Tailscale in userspace-networking mode and reaches the tower 3060
# ONLY via a local SOCKS5 proxy. The tower-floor rung must therefore carry a proxy
# and _call_openai_compatible must apply it to the HTTP call -- but ONLY for a rung
# that specifies one, so cloud rungs behave exactly as before (proxies=None).
# The requests.post is mocked -- no live tower, no network in CI.

class _FakeResp:
    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "pong", "tool_calls": None}}]}


def _capture_post(monkeypatch):
    """Patch requests.post (imported inside the adapter) + rung_quota.record;
    return the list that captures each call's kwargs."""
    captured = []

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None, **kw):
        captured.append({"url": url, "proxies": proxies})
        return _FakeResp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    # rung_quota.record touches r.headers; _FakeResp.headers is a plain dict -> ok.
    monkeypatch.setattr(escalation.rung_quota, "record",
                        lambda *a, **k: None, raising=False)
    return captured


def test_floor_rung_carries_proxy():
    """The tower-floor rung declares a SOCKS proxy on extra_params; it is NOT a
    model-payload parameter (num_ctx stays, proxy is the transport hint)."""
    sp = escalation.LADDER[escalation._FLOOR_RUNG]
    assert sp.extra_params.get("proxy", "").startswith("socks5")
    assert sp.extra_params.get("num_ctx") == 8192


def test_adapter_applies_proxy_only_for_floor_rung(monkeypatch):
    """_call_openai_compatible routes the floor rung's HTTP call through the proxy
    and does NOT leak the proxy into the JSON body; a cloud rung gets proxies=None."""
    monkeypatch.setenv("TOWER_OLLAMA_API_KEY", "dummy")      # adapter key guard
    captured = _capture_post(monkeypatch)

    floor = escalation.LADDER[escalation._FLOOR_RUNG]
    proxy = floor.extra_params["proxy"]
    sent_body = {}

    import requests

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None, **kw):
        captured.append({"url": url, "proxies": proxies})
        sent_body.clear()
        sent_body.update(json or {})
        return _FakeResp()

    monkeypatch.setattr(requests, "post", fake_post)

    text, err, _tc = escalation._call_openai_compatible(
        floor, "hi", None, 16, 0.5, None)
    assert err is None and text == "pong"
    assert captured[-1]["proxies"] == {"http": proxy, "https": proxy}
    # proxy is a transport hint only -- it must not ride in the model payload,
    # while the real model param (num_ctx) must.
    assert "proxy" not in sent_body
    assert sent_body.get("num_ctx") == 8192


def test_adapter_no_proxy_for_cloud_rung(monkeypatch):
    """A cloud rung (BEAM/Reflection ceiling -- no proxy on its spec) calls with
    proxies=None, i.e. direct to the internet, exactly as before this change."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy")
    captured = _capture_post(monkeypatch)

    ceiling = escalation.LADDER[escalation._CEILING_RUNG]
    assert "proxy" not in (ceiling.extra_params or {})      # no proxy on cloud spec

    text, err, _tc = escalation._call_openai_compatible(
        ceiling, "hi", None, 16, 0.5, None)
    assert err is None and text == "pong"
    assert captured[-1]["proxies"] is None                  # DIRECT, unaffected
