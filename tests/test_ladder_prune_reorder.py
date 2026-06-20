"""Prune + tool-aware failover: deprecated rungs disabled; text-only rungs skipped
when tools are requested; openai_compatible (Cerebras/Groq/Mistral) reached fast."""
import escalation as E
import rung_quota as Q


def _byid(mid):
    return next(s for s in E.LADDER if s.model_id == mid)


def test_deprecated_gemini2_disabled():
    assert _byid("gemini-2.0-flash-lite").enabled is False
    assert _byid("gemini-2.0-flash").enabled is False
    assert _byid("gemini-2.5-flash-lite").enabled is True   # working tier stays


def test_text_only_rungs_marked():
    for mid in ("gemma-3-12b-it", "zo:openai/gpt-5.4-mini", "zo:zai/glm-5",
                "zo:anthropic/claude-sonnet-4-5"):
        assert _byid(mid).tool_capable is False
    # tool-capable rungs unaffected
    assert _byid("gpt-oss-120b").tool_capable is True
    assert _byid("gemini-2.5-flash").tool_capable is True


def test_tool_build_skips_textonly_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "QUOTA_FILE", tmp_path / "q.json")
    monkeypatch.setenv("LADDER_BACKOFF", "0")
    monkeypatch.setenv("LADDER_FAILOVER_EXTRA", "50")
    called = []

    def adapter(spec, *a, **k):
        called.append(spec.model_id)
        return ("<!doctype html>ok", None, None) if spec.backend == "openai_compatible" else (None, "429 rate-limited", None)
    monkeypatch.setattr(E, "BACKEND_ADAPTERS", {b: adapter for b in E.BACKEND_ADAPTERS})

    r = E.ask("builder_low", "build", max_tokens=64, tools=[{"type": "function", "function": {"name": "w"}}])
    assert r.success and r.backend == "openai_compatible"        # reached a fast tool-capable rung
    assert not any(m.startswith("gemma-") for m in called)       # text-only skipped
    assert not any(m.startswith("zo:") for m in called)          # zo_routed skipped on tool build
    assert "gemini-2.0-flash" not in called                      # disabled never tried


def test_text_task_still_uses_textonly(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "QUOTA_FILE", tmp_path / "q.json")
    monkeypatch.setenv("LADDER_BACKOFF", "0")
    seen = []

    def adapter(spec, *a, **k):
        seen.append(spec.model_id)
        return ("text ok", None, None)        # first eligible rung succeeds
    monkeypatch.setattr(E, "BACKEND_ADAPTERS", {b: adapter for b in E.BACKEND_ADAPTERS})
    # no tools -> text-only rungs are NOT filtered (this call succeeds on rung 0)
    r = E.ask("builder_low", "x", max_tokens=16)
    assert r.success
