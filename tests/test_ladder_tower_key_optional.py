"""test_ladder_tower_key_optional.py -- the tower (local Ollama) rungs must NOT
require an API key.

Regression lock for the bug that made the tower floor INERT in production: the
hermes3:8b FREE floor rung (and the qwen2.5-coder:14b coder rung) carried
key_env="TOWER_OLLAMA_API_KEY" / "TOWER_CODER_API_KEY", but that var is unset on
zocompute, so _call_openai_compatible returned "<key_env> not set" and the rung
died instantly -- every routine task fell through to Gemini-free / paid BEAM and
the local 3060 was never hit, despite TOWER_OLLAMA_ENABLED=1.

The fix: ModelSpec.key_optional. A keyless/local rung sends a dummy bearer
(Ollama ignores Authorization) instead of failing. Cloud rungs keep
key_optional=False, so their key guard is BYTE-IDENTICAL to before.

Two poles:
  * POLE A -- a key_optional rung with NO key env set: the guard passes and the
    call proceeds (dummy "Bearer ollama"). This is the behaviour that was broken.
  * POLE B (control) -- a non-key_optional (cloud) rung with NO key: STILL returns
    "<key_env> not set". Proves the relaxation is scoped and cloud enforcement is
    unchanged.

Imports only `escalation` (no network / real keys); requests.post is mocked.
"""
from __future__ import annotations

import escalation


# --- the tower rungs are now declared keyless --------------------------------

def test_tower_rungs_are_key_optional():
    floor = escalation.LADDER[escalation._FLOOR_RUNG]
    coder = escalation.LADDER[escalation._CODER_RUNG]
    assert floor.model_id == "hermes3:8b"
    assert coder.model_id == "qwen2.5-coder:14b"
    assert floor.key_optional is True
    assert coder.key_optional is True


def test_modelspec_key_optional_defaults_false():
    # Anything not explicitly marked keyless keeps the strict guard. Spot-check a
    # representative cloud rung (BEAM ceiling via OpenRouter).
    ceiling = escalation.LADDER[escalation._CEILING_RUNG]
    assert ceiling.key_optional is False


def _mock_ok_post(monkeypatch):
    """Patch requests.post to capture the outgoing Authorization header and return
    a minimal OpenAI-shaped 200. Returns the capture list."""
    captured = []

    class _Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "pong", "tool_calls": None}}]}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None, **kw):
        captured.append({"url": url, "headers": headers or {}})
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(escalation.rung_quota, "record",
                        lambda *a, **k: None, raising=False)
    return captured


# --- POLE A: keyless local rung, NO key env => call proceeds with dummy bearer -

def test_floor_rung_proceeds_without_key(monkeypatch):
    monkeypatch.delenv("TOWER_OLLAMA_API_KEY", raising=False)
    captured = _mock_ok_post(monkeypatch)

    floor = escalation.LADDER[escalation._FLOOR_RUNG]
    text, err, _tc = escalation._call_openai_compatible(
        floor, "say pong", None, 16, 0.0, None)

    # The bug was: err == "TOWER_OLLAMA_API_KEY not set" and no request sent.
    assert err is None
    assert text == "pong"
    assert captured, "no request was sent -- the key guard still blocks the rung"
    assert captured[-1]["headers"].get("Authorization") == "Bearer ollama"


def test_coder_rung_proceeds_without_key(monkeypatch):
    monkeypatch.delenv("TOWER_CODER_API_KEY", raising=False)
    captured = _mock_ok_post(monkeypatch)

    coder = escalation.LADDER[escalation._CODER_RUNG]
    text, err, _tc = escalation._call_openai_compatible(
        coder, "write x", None, 16, 0.0, None)
    assert err is None
    assert captured[-1]["headers"].get("Authorization") == "Bearer ollama"


def test_explicit_key_still_used_when_present(monkeypatch):
    # key_optional does not IGNORE a real key -- a set key_env is still sent.
    monkeypatch.setenv("TOWER_OLLAMA_API_KEY", "real-key-123")
    captured = _mock_ok_post(monkeypatch)
    floor = escalation.LADDER[escalation._FLOOR_RUNG]
    _t, err, _tc = escalation._call_openai_compatible(
        floor, "say pong", None, 16, 0.0, None)
    assert err is None
    assert captured[-1]["headers"].get("Authorization") == "Bearer real-key-123"


# --- POLE B (control): cloud rung, NO key => STILL fails, guard unchanged ------

def test_cloud_rung_still_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # If the guard were wrongly relaxed for everyone, this would send a request;
    # mock post so a regression is loud rather than a real network call.
    captured = _mock_ok_post(monkeypatch)

    ceiling = escalation.LADDER[escalation._CEILING_RUNG]
    assert ceiling.key_optional is False
    text, err, _tc = escalation._call_openai_compatible(
        ceiling, "hi", None, 16, 0.0, None)

    assert text is None
    assert err == "OPENROUTER_API_KEY not set"
    assert not captured, "cloud rung sent a request despite no key -- guard weakened"
