"""FU-027 + FU-056 regression tests for tools/rescore/weekly_rescore.py.

FU-056: run 20260719-003024 opened, fired, paid $1.19, imported ~half its 65,045-server
cohort, died, and never closed. The 8-day liveness rule asks "was there a recent
SUCCESS?" -- it answered yes (7/18) and missed this completely, so ~32,545 servers
served a stale wave for two days. These tests pin the ledger-shaped check instead.

FU-027: the proximate killer was `urlopen(FRESHNESS_URL, timeout=30)` in ph_postcheck,
a READ-ONLY call that fired after import and backfill had already committed. These
tests pin that a freshness failure can no longer strand a run.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ FU-056
def test_closed_run_is_not_reported_open(wr, tmp_path):
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": _ts(now - timedelta(hours=5)), "run": "r1", "event": "run_opened"},
        {"ts": _ts(now - timedelta(hours=4)), "run": "r1", "event": "phase_import_done"},
        {"ts": _ts(now - timedelta(hours=3)), "run": "r1", "event": "run_closed"},
    ])
    assert wr.open_runs(p, now=now) == []


def test_the_real_20260719_shape_is_caught(wr, tmp_path):
    """The exact shape that slipped through: destroy done, then silence, no close.

    A prior SUCCESSFUL run sits in the same ledger -- that success is precisely what
    made the 8-day liveness rule report green, so it must not mask the open run.
    """
    now = datetime(2026, 7, 21, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-17T18:29:21Z", "run": "20260717-182921", "event": "run_opened"},
        {"ts": "2026-07-18T23:43:21Z", "run": "20260717-182921", "event": "run_closed"},
        {"ts": "2026-07-19T00:30:24Z", "run": "20260719-003024", "event": "run_opened"},
        {"ts": "2026-07-19T05:17:31Z", "run": "20260719-003024",
         "event": "phase_destroy_done"},
    ])
    got = wr.open_runs(p, now=now)
    assert [r["run_id"] for r in got] == ["20260719-003024"]
    assert got[0]["last_event"] == "phase_destroy_done"
    assert got[0]["outcome"] == "stranded"
    assert got[0]["stale"] is True
    assert got[0]["open_hours"] == pytest.approx(53.5, abs=0.6)
    assert wr.check_open_runs(p) == 1


@pytest.mark.parametrize("abort_event", [
    "wedge_guard_destroy",
    "wedge_destroy_manual",
    "wedge_check_closed_run",
    "manual_destroy_none_state",
    "manual_destroy_container_start_failed",
])
def test_deliberately_aborted_runs_do_not_alarm(wr, tmp_path, abort_event):
    """Ten runs in the live ledger ended this way. A detector that alarms on all of
    them every run is decorative, which is the failure class this check exists to
    avoid -- so aborts are classified, not counted as stranded."""
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-17T15:12:56Z", "run": "a1", "event": "run_opened"},
        {"ts": "2026-07-17T16:46:10Z", "run": "a1", "event": abort_event},
    ])
    assert wr.open_runs(p, now=now) == []
    got = wr.open_runs(p, now=now, include_aborted=True)
    assert len(got) == 1
    assert got[0]["outcome"] == "aborted" and got[0]["stale"] is False
    assert wr.check_open_runs(p) == 0


def test_mid_pipeline_teardown_is_not_an_abort(wr, tmp_path):
    """`phase_destroy_done` and `destroyed` fire BEFORE import/backfill/postcheck.
    Treating either as terminal would re-create the exact blindness of 7/19."""
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    for event in ("phase_destroy_done", "destroyed"):
        p = _ledger(tmp_path / event, [
            {"ts": "2026-07-19T00:30:24Z", "run": "s1", "event": "run_opened"},
            {"ts": "2026-07-19T05:17:31Z", "run": "s1", "event": event},
        ]) if (tmp_path / event).mkdir() or True else None
        got = wr.open_runs(p, now=now)
        assert [r["outcome"] for r in got] == ["stranded"], event
        assert got[0]["stale"] is True, event


def test_fresh_open_run_is_reported_but_not_stale(wr, tmp_path):
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": _ts(now - timedelta(hours=2)), "run": "r9", "event": "run_opened"},
    ])
    got = wr.open_runs(p, now=now)
    assert len(got) == 1 and got[0]["stale"] is False


def test_torn_line_does_not_blind_the_detector(wr, tmp_path):
    """A half-written JSON line must not swallow the open run that follows it."""
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        json.dumps({"ts": _ts(now - timedelta(hours=40)), "run": "r1",
                    "event": "run_opened"}) + "\n"
        + '{"ts": "2026-07-20T01:00:00Z", "run": "r1", "eve\n'
        + "\n",
        encoding="utf-8")
    got = wr.open_runs(p, now=now)
    assert [r["run_id"] for r in got] == ["r1"] and got[0]["stale"] is True


def test_missing_ledger_is_not_an_error(wr, tmp_path):
    assert wr.open_runs(tmp_path / "nope.jsonl") == []


def test_check_exit_codes(wr, tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    stale = _ledger(tmp_path, [
        {"ts": _ts(now - timedelta(hours=72)), "run": "old", "event": "run_opened"}])
    assert wr.check_open_runs(stale) == 1
    sub = tmp_path / "b"
    sub.mkdir()
    fresh = _ledger(sub, [
        {"ts": _ts(now - timedelta(hours=1)), "run": "new", "event": "run_opened"}])
    assert wr.check_open_runs(fresh) == 0
    assert wr.check_open_runs(tmp_path / "absent.jsonl") == 0


# ------------------------------------------------------------------ FU-027
def test_freshness_retries_then_succeeds(wr, monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("The read operation timed out")
        return _FakeResp({"scored_servers": 172295})

    monkeypatch.setattr(wr, "FRESHNESS_BACKOFF", 0)
    monkeypatch.setitem(__import__("urllib.request").request.__dict__,
                        "urlopen", flaky)
    assert wr.freshness()["scored_servers"] == 172295
    assert calls["n"] == 3


def test_freshness_timeout_budget_exceeds_observed_cold_path(wr):
    """The cold path was measured at 20-300s; 30s was BELOW it, which is why a miss
    failed deterministically rather than occasionally."""
    assert wr.FRESHNESS_TIMEOUT > 45


def test_freshness_safe_never_raises(wr, monkeypatch):
    def dead(*a, **kw):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(wr, "FRESHNESS_BACKOFF", 0)
    monkeypatch.setitem(__import__("urllib.request").request.__dict__,
                        "urlopen", dead)
    payload, err = wr.freshness_safe()
    assert payload == {}
    # The error is surfaced, not swallowed -- a degraded postcheck must say why.
    assert "unreachable after 3 attempt(s)" in err
    assert "The read operation timed out" in err


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
