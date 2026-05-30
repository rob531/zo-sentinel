"""
test_breaker_retire.py -- hermetic tests for permanent retirement of dead
rebuild targets (gate_quality_state.retire / unretire).

Retirement is distinct from quarantine: quarantine is a recoverable "failed too
much, hold off" state the generator still churns on; retirement says "stop
proposing this at all" -- for one-shot patchers that already ran, or check/test
scripts now owned by the GitHub CI gates. Same host-free harness as
test_breaker_autorecover (env-repointed state file + module reload).
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

OLD = "2026-01-01T00:00:00+00:00"


def _load(monkeypatch, tmp_path):
    state = tmp_path / "gate_quality_state.json"
    monkeypatch.setenv("GATE_QUALITY_STATE_FILE", str(state))
    monkeypatch.setenv("BREAKER_AUTO_RECOVER_SECS", "0")  # isolate from auto-recover
    import gate_quality_state as m
    importlib.reload(m)
    return m


def _write(m, **over):
    base = {
        "state": "closed",
        "state_changed_at": OLD,
        "state_changed_reason": "test",
        "recent_cohorts": [],
        "file_retries": {},
        "quarantined": {},
        "retired": {},
        "notes": [],
    }
    base.update(over)
    Path(os.environ["GATE_QUALITY_STATE_FILE"]).write_text(json.dumps(base, indent=2))


def test_retire_blocks_rebuild_with_reason(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)
    m.retire("github_pr_checker_wiring.py", "owned by pr-gates.yml")
    assert m.is_retired("github_pr_checker_wiring.py")
    ok, reason = m.may_rebuild("github_pr_checker_wiring.py")
    assert ok is False
    assert "retired" in reason and "pr-gates.yml" in reason


def test_retire_clears_quarantine_and_retries(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m,
           quarantined={"x.py": {"quarantined_at": OLD, "reason": "3 fails",
                                 "attempts_when_quarantined": 3}},
           file_retries={"x.py": {"attempts": 3, "last_failed_at": OLD,
                                  "last_error": "boom", "cohorts": []}})
    m.retire("x.py", "dead one-shot patcher")
    snap = m.snapshot()
    assert "x.py" in snap["retired"]
    assert "x.py" not in snap["quarantined"]   # dropped from active accounting
    assert "x.py" not in snap["file_retries"]


def test_retire_is_idempotent(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)
    first = m.retire("x.py", "first reason")
    second = m.retire("x.py", "second reason")
    assert first["retired_at"] == second["retired_at"]   # timestamp preserved
    assert m.snapshot()["retired"]["x.py"]["reason"] == "first reason"


def test_unretire_restores_rebuild(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)
    m.retire("x.py", "oops")
    assert m.unretire("x.py", "back in scope") is True
    assert not m.is_retired("x.py")
    ok, reason = m.may_rebuild("x.py")
    assert ok is True and reason == "ok"


def test_unretire_noop_when_not_retired(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)
    assert m.unretire("never.py") is False


def test_retired_beats_tripped_in_reason(monkeypatch, tmp_path):
    """A retired file reports 'retired', not 'tripped', even while tripped."""
    m = _load(monkeypatch, tmp_path)
    _write(m, state="tripped", retired={"x.py": {"retired_at": OLD, "reason": "dead"}})
    ok, reason = m.may_rebuild("x.py")
    assert ok is False and "retired" in reason
    # a non-retired file still reports the trip
    ok2, reason2 = m.may_rebuild("other.py")
    assert ok2 is False and "tripped" in reason2


def test_forward_compat_state_without_retired_key(monkeypatch, tmp_path):
    """A pre-existing state file with no 'retired' key still works."""
    m = _load(monkeypatch, tmp_path)
    legacy = {"state": "closed", "state_changed_at": OLD, "state_changed_reason": "x",
              "recent_cohorts": [], "file_retries": {}, "quarantined": {}, "notes": []}
    Path(os.environ["GATE_QUALITY_STATE_FILE"]).write_text(json.dumps(legacy))
    assert m.is_retired("x.py") is False
    m.retire("x.py", "now retired")
    assert m.is_retired("x.py") is True
