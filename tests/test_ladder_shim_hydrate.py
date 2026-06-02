"""
Regression guard for the recurring keyless-shim bug: ladder_shim must self-hydrate
the LLM keys into its own env at startup, so it's keyed however it's launched
(bare `python3 ladder_shim.py`, crash/watchdog restart, not only via
ladder_shim_with_keys.sh). Without it, escalation sees 'RcGeminiAPIKey not set'
and every rung above MiniMax 502s.

The shim imports fastapi/uvicorn/escalation, so we skip cleanly where those
aren't available (e.g. a minimal pytest env).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
try:
    import ladder_shim  # noqa: E402
except Exception as e:  # escalation or its deps unavailable here
    pytest.skip(f"ladder_shim not importable in this env: {e}", allow_module_level=True)


def _clear(monkeypatch):
    for k in ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "RcGeminiAPIKey"):
        monkeypatch.delenv(k, raising=False)


def test_self_hydrate_sets_and_mirrors(monkeypatch):
    _clear(monkeypatch)
    fake = {"MINIMAX_API_KEY": "mm", "GEMINI_API_KEY": "AIzaTEST", "ANTHROPIC_API_KEY": "ant"}
    ok = ladder_shim._self_hydrate_keys(runner=lambda k: fake.get(k, ""), sleep=lambda *_: None)
    assert ok is True
    assert os.environ["GEMINI_API_KEY"] == "AIzaTEST"
    assert os.environ["RcGeminiAPIKey"] == "AIzaTEST"   # mirrored for escalation
    assert os.environ["MINIMAX_API_KEY"] == "mm"


def test_self_hydrate_keeps_existing_and_skips_fetch(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaPRESET")
    fetched = []

    def runner(k):
        fetched.append(k)
        return "SHOULD_NOT_BE_USED"

    ladder_shim._self_hydrate_keys(runner=runner, sleep=lambda *_: None)
    assert os.environ["GEMINI_API_KEY"] == "AIzaPRESET"   # not overwritten
    assert "GEMINI_API_KEY" not in fetched                 # already set -> not re-fetched
    assert os.environ["RcGeminiAPIKey"] == "AIzaPRESET"   # still mirrored


def test_self_hydrate_warns_and_returns_false_when_unresolved(monkeypatch):
    _clear(monkeypatch)
    ok = ladder_shim._self_hydrate_keys(runner=lambda k: "", sleep=lambda *_: None, attempts=2)
    assert ok is False
    assert not os.environ.get("RcGeminiAPIKey")
