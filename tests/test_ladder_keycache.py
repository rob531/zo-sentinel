"""Tests for ladder_shim key-cache + raised hydrate timeout (durable fix for the
recurring key_hydrator 30s-timeout -> rung-502 ghost stall). A warm cache lets a
watchdog-restarted shim key itself instantly without the slow Tower round-trip."""
import os
import pytest


def _ls():
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    import ladder_shim
    return ladder_shim


def test_cache_write_then_load_roundtrip(tmp_path, monkeypatch):
    ls = _ls()
    cache = tmp_path / "k.cache"
    for k in ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "v_" + k)
    ls._write_key_cache(cache)
    assert cache.exists()
    assert oct(cache.stat().st_mode & 0o777) == "0o600"
    for k in ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    loaded = ls._load_key_cache(cache)
    assert loaded == {"MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"}
    assert os.environ["GEMINI_API_KEY"] == "v_GEMINI_API_KEY"


def test_load_does_not_clobber_existing_env(tmp_path, monkeypatch):
    ls = _ls()
    cache = tmp_path / "k.cache"
    cache.write_text("GEMINI_API_KEY=fromcache\n")
    monkeypatch.setenv("GEMINI_API_KEY", "fromenv")
    loaded = ls._load_key_cache(cache)
    assert "GEMINI_API_KEY" not in loaded
    assert os.environ["GEMINI_API_KEY"] == "fromenv"


def test_self_hydrate_warm_cache_skips_subprocess(tmp_path, monkeypatch):
    ls = _ls()
    cache = tmp_path / "k.cache"
    cache.write_text("MINIMAX_API_KEY=m\nGEMINI_API_KEY=g\nANTHROPIC_API_KEY=a\n")
    for k in ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "RcGeminiAPIKey"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ls, "KEY_CACHE_FILE", cache)

    def boom(_k):
        raise AssertionError("key_hydrator must not be called on a warm cache hit")

    ok = ls._self_hydrate_keys(runner=boom, attempts=1, sleep=lambda *_: None)
    assert ok is True
    assert os.environ.get("RcGeminiAPIKey") == "g"


def test_self_hydrate_writes_cache_after_resolve(tmp_path, monkeypatch):
    ls = _ls()
    cache = tmp_path / "out.cache"
    for k in ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "RcGeminiAPIKey"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ls, "KEY_CACHE_FILE", cache)
    vals = {"MINIMAX_API_KEY": "m", "GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}
    ok = ls._self_hydrate_keys(runner=lambda k: vals[k], attempts=1, sleep=lambda *_: None)
    assert ok is True
    assert cache.exists()
    body = cache.read_text()
    assert "GEMINI_API_KEY=g" in body and "MINIMAX_API_KEY=m" in body
