"""The bus snapshot's age must be a VERDICT, not a footnote.

WHY THIS FILE EXISTS
    tools/referent_verify.py resolves tables and columns against
    schema/bus_catalog.json, a snapshot refreshed on the host because only the
    host can reach the write-service bus. That snapshot is the seam, and a seam
    that can rot silently is the 2026-04 E2E runner all over again.

    It already refused to pass on a snapshot past its budget. What it did NOT
    do was say WHICH kind of "cannot evaluate" that was. "absent", "unparseable",
    "undated" and "fourteen days old" were one undifferentiated UNKNOWN. Only
    the last one names a daemon that stopped and carries a number saying how
    long ago, and folding it in threw both away.

    This matters more now than while the check was report-only. referent-verify
    is a REQUIRED status check (#4089). When the snapshot passes the budget,
    tables/columns go red and every pull request on the repository is blocked.
    That is a repo-wide outage arriving as a cliff, so there is now a WARN band
    a week wide in front of it.

THREE BANDS
    age <= 7d   OK
    7d < age <= 14d   WARN  -- reported, with the days remaining
    age > 14d         STALE -- its own verdict, blocking, carrying the age
"""
import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _rv():
    spec = importlib.util.spec_from_file_location(
        "referent_verify", ROOT / "tools" / "referent_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RV = _rv()


def _snapshot(tmp_path, age_days, monkeypatch, captured="auto"):
    """Point RV at a copy of the real snapshot aged by `age_days`."""
    real = json.loads((ROOT / "schema" / "bus_catalog.json").read_text())
    if captured == "auto":
        captured = (datetime.now(timezone.utc)
                    - timedelta(days=age_days)).isoformat()
    real["captured_at"] = captured
    p = tmp_path / "bus_catalog.json"
    p.write_text(json.dumps(real))
    monkeypatch.setattr(RV, "BUS_CATALOG", p)
    return RV.load_catalog()


def test_thresholds_are_ordered():
    assert 0 < RV.SNAPSHOT_WARN_AGE_DAYS < RV.SNAPSHOT_MAX_AGE_DAYS


def test_fresh_snapshot_resolves_and_is_neither_warn_nor_stale(tmp_path, monkeypatch):
    tables, meta, reason = _snapshot(tmp_path, 1, monkeypatch)
    assert reason is None
    assert tables
    assert meta["bus_stale"] is False
    assert meta["bus_warn"] is False


def test_warn_band_still_resolves(tmp_path, monkeypatch):
    """WARN is notice, not refusal. The catalog is still usable at 9 days."""
    tables, meta, reason = _snapshot(tmp_path, 9, monkeypatch)
    assert reason is None, "a warn-band snapshot must still render a verdict"
    assert tables
    assert meta["bus_warn"] is True
    assert meta["bus_stale"] is False


def test_over_budget_is_stale_and_carries_the_age(tmp_path, monkeypatch):
    tables, meta, reason = _snapshot(tmp_path, 20, monkeypatch)
    assert tables == {}
    assert meta["bus_stale"] is True
    assert reason.startswith(RV.STALE_PREFIX), \
        "staleness must be distinguishable from every other UNKNOWN"
    assert "20.0 days old" in reason, "the verdict must carry the age"
    assert str(RV.SNAPSHOT_MAX_AGE_DAYS) in reason
    assert "not a pass" in reason.lower()


def test_stale_is_never_silently_a_pass(tmp_path, monkeypatch):
    _t, _m, reason = _snapshot(tmp_path, 400, monkeypatch)
    assert reason is not None


def test_undated_snapshot_is_unknown_not_stale(tmp_path, monkeypatch):
    """The other unknowns keep their own identity.

    An undated snapshot is not an old one -- it is one whose age nobody can
    compute, and the fix is different. Collapsing them is the bug this change
    exists to undo, in the opposite direction.
    """
    real = json.loads((ROOT / "schema" / "bus_catalog.json").read_text())
    real.pop("captured_at", None)
    p = tmp_path / "bus_catalog.json"
    p.write_text(json.dumps(real))
    monkeypatch.setattr(RV, "BUS_CATALOG", p)
    _t, _m, reason = RV.load_catalog()
    assert reason and not reason.startswith(RV.STALE_PREFIX)
    assert "captured_at" in reason


def test_missing_snapshot_is_unknown_not_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(RV, "BUS_CATALOG", tmp_path / "nope.json")
    _t, _m, reason = RV.load_catalog()
    assert reason and not reason.startswith(RV.STALE_PREFIX)


def test_the_committed_snapshot_on_this_branch_is_not_stale():
    """Guards the repo's own state, not just the logic."""
    _t, meta, reason = RV.load_catalog()
    assert reason is None or not reason.startswith(RV.STALE_PREFIX), \
        f"the committed bus catalog is STALE right now: {reason}"
    assert meta["bus_age_days"] <= RV.SNAPSHOT_MAX_AGE_DAYS
