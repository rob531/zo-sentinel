"""test_ladder_coder_local.py -- the LOCAL $0 coding rung (qwen2.5-coder:14b).

Locks in the local coder change (additive to the two-tier ladder):
  * A tower RTX 3060 Ollama rung serving qwen2.5-coder:14b -- a FREE
    (cost_priority 0.0) code-specialist, GATED OFF by TOWER_CODER_ENABLED
    (default unset). With the flag unset the rung is pruned and routing is
    IDENTICAL to today (fail-graceful proof); MiniMax's builder start tiers are
    NEVER displaced.
  * A dedicated `code_local` task type that STARTS on the coder rung when the
    flag is set, and falls THROUGH to MiniMax/cloud otherwise.
  * FAIL-GRACEFUL TIMEOUT: a timeout / connection error on the local coder rung
    falls through to the next rung -- the task still completes.
  * CLEAN SYNTAX OUTPUT: a response with a <think> block + prose around a fenced
    code block is reduced to just the code; cloud rungs are untouched.

Imports only `escalation` (no fastapi/uvicorn / network / keys). Env is
monkeypatched + apply_two_tier_routing() re-run, so module import-time state is
never relied on.
"""
from __future__ import annotations

import escalation


def _reset(monkeypatch, *, coder=False):
    if coder:
        monkeypatch.setenv("TOWER_CODER_ENABLED", "1")
    else:
        monkeypatch.delenv("TOWER_CODER_ENABLED", raising=False)
    # keep the other two tiers off so this file only exercises the coder
    monkeypatch.delenv("TOWER_OLLAMA_ENABLED", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    escalation.apply_two_tier_routing()


# --- the coder rung exists, appended last, correctly configured ---------------

def test_coder_rung_present_and_configured():
    sp = escalation.LADDER[escalation._CODER_RUNG]
    assert sp.backend == "openai_compatible"
    assert sp.model_id == "qwen2.5-coder:14b"
    assert sp.cost_priority == 0.0                      # a FREE / $0 rung
    assert sp.key_env == "TOWER_CODER_API_KEY"
    assert "11434" in sp.base_url                       # tower Ollama endpoint


def test_coder_rung_carries_proxy_timeout_and_code_only():
    sp = escalation.LADDER[escalation._CODER_RUNG]
    # proxy = reach the 3060 over zocompute's userspace Tailscale SOCKS proxy
    assert sp.extra_params.get("proxy", "").startswith("socks5")
    # SHORT per-request timeout so an unreachable/busy tower never blocks builds
    assert 0 < float(sp.extra_params.get("timeout", 0)) <= 60
    # code-only post-processing is this rung's behaviour
    assert sp.extra_params.get("code_only") is True


# --- FAIL-GRACEFUL: flag unset => identical to today, MiniMax undisplaced ------

def test_coder_disabled_and_minimax_unchanged_when_flag_unset(monkeypatch):
    _reset(monkeypatch, coder=False)
    assert escalation.LADDER[escalation._CODER_RUNG].enabled is False
    # code_local falls back to rung 0 (MiniMax) when the coder is off
    assert escalation.TASK_START_TIER["code_local"] == 0
    # MiniMax builder start tiers are NOT touched by this change
    assert escalation.TASK_START_TIER["builder"] == 0
    assert escalation.TASK_START_TIER["builder_low"] == 0
    assert escalation.TASK_START_TIER["builder_medium"] == 1
    assert escalation.TASK_START_TIER["builder_high"] == 11
    assert escalation.TASK_START_TIER["builder_critical"] == 15


def test_coder_routing_reversible(monkeypatch):
    _reset(monkeypatch, coder=True)
    assert escalation.LADDER[escalation._CODER_RUNG].enabled is True
    assert escalation.TASK_START_TIER["code_local"] == escalation._CODER_RUNG
    _reset(monkeypatch, coder=False)
    assert escalation.LADDER[escalation._CODER_RUNG].enabled is False
    assert escalation.TASK_START_TIER["code_local"] == 0


# --- FLAG ON: lighter coding starts on the coder, MiniMax still undisplaced ----

def test_coder_is_start_tier_for_code_local_when_enabled(monkeypatch):
    _reset(monkeypatch, coder=True)
    assert escalation.LADDER[escalation._CODER_RUNG].enabled is True
    assert escalation.TASK_START_TIER["code_local"] == escalation._CODER_RUNG
    # MiniMax builder start tiers remain exactly where they were (ADDITIVE)
    assert escalation.TASK_START_TIER["builder"] == 0
    assert escalation.TASK_START_TIER["builder_low"] == 0
    assert escalation.TASK_START_TIER["builder_medium"] == 1
    assert escalation.TASK_START_TIER["builder_high"] == 11
    assert escalation.TASK_START_TIER["builder_critical"] == 15


def test_model_alias_maps_to_code_local():
    assert escalation.task_for_model("zo-ladder-code-local") == "code_local"
    # MiniMax builder aliases unchanged
    assert escalation.task_for_model("zo-ladder-v1") == "builder"
    assert escalation.task_for_model("zo-ladder-medium") == "builder_medium"


def test_ask_starts_code_local_on_coder_when_enabled(monkeypatch):
    """End-to-end through ask(): an enabled coder makes code_local START on the
    qwen rung (adapters stubbed -> no network)."""
    _reset(monkeypatch, coder=True)
    called = []

    def fake_adapter(spec, prompt, system, max_tokens, temperature, tools):
        called.append(spec.model_id)
        return ("def f(): pass", None, None)

    monkeypatch.setattr(escalation, "BACKEND_ADAPTERS",
                        {k: fake_adapter for k in escalation.BACKEND_ADAPTERS})
    r = escalation.ask("code_local", "write a function", max_attempts=4)
    assert r.success
    assert called[0] == "qwen2.5-coder:14b"
    _reset(monkeypatch, coder=False)


# --- FAIL-GRACEFUL TIMEOUT: a hung/unreachable tower escalates to the next rung -

def test_coder_timeout_falls_through_to_next_rung(monkeypatch):
    """A ReadTimeout / ConnectionError on the local coder rung must fall through
    to the next rung so the task STILL COMPLETES -- the local rung never blocks
    the builder."""
    _reset(monkeypatch, coder=True)
    monkeypatch.setenv("LADDER_BACKOFF", "0")
    called = []

    def flaky_adapter(spec, prompt, system, max_tokens, temperature, tools):
        called.append(spec.model_id)
        if spec.model_id == "qwen2.5-coder:14b":
            # mirror what _call_openai_compatible returns on a requests timeout:
            # (None, "<ExceptionName>: ...", None) -> the ladder escalates.
            return (None, "ReadTimeout: tower unreachable/busy", None)
        return ("built elsewhere", None, None)

    monkeypatch.setattr(escalation, "BACKEND_ADAPTERS",
                        {k: flaky_adapter for k in escalation.BACKEND_ADAPTERS})
    r = escalation.ask("code_local", "write a function", max_attempts=4)
    assert r.success                                   # task STILL completes
    assert called[0] == "qwen2.5-coder:14b"            # coder tried first
    assert r.model != "qwen2.5-coder:14b"              # then escalated past it
    assert r.text == "built elsewhere"
    _reset(monkeypatch, coder=False)


def test_adapter_short_timeout_on_coder_and_direct_cloud(monkeypatch):
    """_call_openai_compatible honours the coder rung's SHORT per-request timeout
    (via extra_params) and routes it through the proxy; a cloud rung keeps the
    240s default and goes direct (proxies=None). requests.post is mocked."""
    monkeypatch.setenv("TOWER_CODER_API_KEY", "dummy")
    captured = []

    class _Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "```py\nx=1\n```",
                                             "tool_calls": None}}]}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None, **kw):
        captured.append({"timeout": timeout, "proxies": proxies})
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(escalation.rung_quota, "record",
                        lambda *a, **k: None, raising=False)

    coder = escalation.LADDER[escalation._CODER_RUNG]
    text, err, _tc = escalation._call_openai_compatible(
        coder, "write x", None, 64, 0.5, None)
    assert err is None
    assert captured[-1]["timeout"] == coder.extra_params["timeout"]   # SHORT, not 240
    assert captured[-1]["timeout"] <= 60
    assert captured[-1]["proxies"]["http"] == coder.extra_params["proxy"]
    # code-only post-processing kicked in: fenced body only
    assert text == "x=1"


# --- CLEAN SYNTAX OUTPUT: <think> + prose around a fenced block => code only ----

def test_extract_code_only_strips_think_and_prose():
    raw = (
        "<think>\nThe user wants an add function. Let me reason about edge cases.\n"
        "</think>\n"
        "Sure! Here is the implementation you asked for:\n\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n\n"
        "This handles ints and floats. Let me know if you need more!"
    )
    out = escalation._extract_code_only(raw)
    assert out == "def add(a, b):\n    return a + b"
    assert "<think>" not in out
    assert "Sure!" not in out
    assert "This handles" not in out
    assert "```" not in out


def test_extract_code_only_multiple_blocks():
    raw = ("prose\n```python\nimport os\n```\nmore prose\n"
           "```python\nprint(os.getcwd())\n```\ntrailing")
    out = escalation._extract_code_only(raw)
    assert out == "import os\n\nprint(os.getcwd())"
    assert "prose" not in out


def test_extract_code_only_no_fence_returns_reasoning_stripped():
    # No fenced block: keep the (reasoning-stripped) text rather than destroy it.
    raw = "<think>planning</think>\ndef f():\n    return 1"
    out = escalation._extract_code_only(raw)
    assert "<think>" not in out
    assert "def f():" in out


def test_cloud_rung_output_not_code_stripped(monkeypatch):
    """The code-only post-processing is rung-local: a cloud rung (no code_only on
    its spec) returns its content verbatim-normalized, prose intact."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy")

    class _Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "Here is prose and then ```py\nx=1\n``` done.",
                "tool_calls": None}}]}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None, **kw):
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(escalation.rung_quota, "record",
                        lambda *a, **k: None, raising=False)

    ceiling = escalation.LADDER[escalation._CEILING_RUNG]
    assert "code_only" not in (ceiling.extra_params or {})
    text, err, _tc = escalation._call_openai_compatible(
        ceiling, "hi", None, 64, 0.5, None)
    assert err is None
    # prose preserved -- NOT reduced to just the fenced body
    assert "Here is prose" in text
