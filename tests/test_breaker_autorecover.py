"""
test_breaker_autorecover.py -- hermetic tests for the Gate-8 breaker's
auto-recovery and stale-quarantine revalidation (gate_quality_state.py).

These are host-free: gate_quality_state is repointed at a tmp state file via
GATE_QUALITY_STATE_FILE, and the module is reloaded so its env-derived
module-level constants (STATE_FILE, AUTO_RECOVER_AFTER_SECS) rebind. fcntl is
optional in the module, so this runs on Windows/CI as well as the host.

Why this exists: a tripped breaker starves its own recovery signal (the
generator stops proposing rebuilds -> no fresh cohorts -> a clean cohort can
never auto-close it), so it sat tripped for ~6 days waiting on a human while
every post-trip cohort was clean. maybe_auto_recover() breaks that deadlock;
release_stale_missing() clears 'missing_on_disk' quarantines whose file exists.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

OLD = "2026-01-01T00:00:00+00:00"   # well past any auto-recover window


def _load(monkeypatch, tmp_path, recover_secs="21600"):
    """Repoint gate_quality_state at a tmp state file and reload it so the
    env-derived constants rebind. Returns the fresh module."""
    state = tmp_path / "gate_quality_state.json"
    monkeypatch.setenv("GATE_QUALITY_STATE_FILE", str(state))
    monkeypatch.setenv("BREAKER_AUTO_RECOVER_SECS", recover_secs)
    import gate_quality_state as m
    importlib.reload(m)
    return m


def _write(m, **over):
    base = {
        "state": "tripped",
        "state_changed_at": OLD,
        "state_changed_reason": "test trip",
        "recent_cohorts": [],
        "file_retries": {},
        "quarantined": {},
        "notes": [],
    }
    base.update(over)
    Path(os.environ["GATE_QUALITY_STATE_FILE"]).write_text(json.dumps(base, indent=2))


# ── auto-recover ──────────────────────────────────────────────────────────────

def test_stale_trip_steps_to_half_open(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)  # tripped, old, no cohorts
    assert m.maybe_auto_recover() == "half-open"
    snap = m.snapshot()
    assert snap["state"] == "half-open"
    assert any(n.get("action") == "auto_recover" for n in snap["notes"])


def test_recent_trip_does_not_recover(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m, state_changed_at=m._now())  # just tripped
    assert m.maybe_auto_recover() is None
    assert m.snapshot()["state"] == "tripped"


def test_failing_cohort_since_trip_blocks_recovery(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    # old trip, but a cohort observed AFTER the trip failed -> still live
    _write(m, recent_cohorts=[{
        "id": "c1", "size": 5, "fail_rate": 0.5,
        "observed_at": "2026-05-01T00:00:00+00:00",
    }])
    assert m.maybe_auto_recover() is None
    assert m.snapshot()["state"] == "tripped"


def test_clean_cohorts_since_trip_allow_recovery(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    # old trip with only clean post-trip cohorts -> the real-world stale case
    _write(m, recent_cohorts=[{
        "id": "c1", "size": 5, "fail_rate": 0.0,
        "observed_at": "2026-05-02T00:00:00+00:00",
    }])
    assert m.maybe_auto_recover() == "half-open"


def test_disabled_when_secs_zero(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, recover_secs="0")
    _write(m)
    assert m.maybe_auto_recover() is None
    assert m.snapshot()["state"] == "tripped"


def test_may_rebuild_triggers_recovery(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m)
    ok, _reason = m.may_rebuild("some_new_file.py")
    # half-open allows non-quarantined files; state must have transitioned
    assert m.snapshot()["state"] == "half-open"
    assert ok is True


def test_half_open_closes_on_clean_cohort(monkeypatch, tmp_path):
    """Regression guard: existing record_cohort half-open->closed still works."""
    m = _load(monkeypatch, tmp_path)
    _write(m, state="half-open", state_changed_at=m._now())
    m.record_cohort("clean1", size=m.MIN_COHORT_SIZE, fail_rate=0.0)
    assert m.snapshot()["state"] == "closed"


# ── stale-quarantine revalidation ─────────────────────────────────────────────

def test_release_stale_missing_releases_present_files(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m, quarantined={
        "present.py": {"quarantined_at": OLD, "reason": "missing_on_disk after 3 fails",
                       "attempts_when_quarantined": 3},
        "gone.py": {"quarantined_at": OLD, "reason": "missing_on_disk after 3 fails",
                    "attempts_when_quarantined": 3},
        "broken.py": {"quarantined_at": OLD, "reason": "3 consecutive fails: cohort_x",
                      "attempts_when_quarantined": 3},
    })
    released = m.release_stale_missing(exists_fn=lambda p: p.endswith("present.py"),
                                       root=tmp_path)
    assert released == ["present.py"]
    q = m.snapshot()["quarantined"]
    assert "present.py" not in q          # released (exists)
    assert "gone.py" in q                 # kept (still missing)
    assert "broken.py" in q               # kept (not a missing_on_disk reason)


def test_release_stale_missing_noop_when_all_absent(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    _write(m, quarantined={
        "gone.py": {"quarantined_at": OLD, "reason": "missing_on_disk after 3 fails",
                    "attempts_when_quarantined": 3},
    })
    assert m.release_stale_missing(exists_fn=lambda p: False, root=tmp_path) == []
    assert "gone.py" in m.snapshot()["quarantined"]
