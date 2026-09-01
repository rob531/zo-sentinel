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


# ------------------------------------------------------------------ FU-132
# The detector classified two ZERO-SPEND runs as STRANDED for 60h, so
# `--check-open-runs` exited 1 on every invocation. These pin the reconciliation
# AND, more importantly, pin the cases where it must still go RED.
def _state(runs_root: Path, rid: str, **fields) -> None:
    d = runs_root / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"run_id": rid, **fields}),
                                  encoding="utf-8")


def test_preflight_kill_recorded_only_in_state_is_not_stranded(wr, tmp_path):
    """The live 7/25 shape: killed at preflight, no export, no instance, $0.

    The operator's last word went to state.json (`result: killed_*`), never to the
    ledger's `wedge_*`/`manual_*` vocabulary, so the detector alarmed forever.
    """
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-25T17:05:56Z", "run": "20260725-170556", "event": "run_opened"},
        {"ts": "2026-07-25T17:09:47Z", "run": "20260725-170556",
         "event": "phase_preflight_done"},
    ])
    _state(tmp_path, "20260725-170556", result="killed_regate_size_scaled",
           phases={"preflight": "done", "postcheck": "done"}, scored_before=278026)
    assert wr.open_runs(p, now=now) == []
    got = wr.open_runs(p, now=now, include_aborted=True)
    assert [r["outcome"] for r in got] == ["aborted"] and got[0]["stale"] is False
    assert wr.check_open_runs(p) == 0


def test_state_result_cannot_excuse_a_run_still_holding_an_instance(wr, tmp_path):
    """THE bar this reconciliation must never lower.

    `check_open_runs` exists for a run that "opened, SPENT, and never closed". A
    `killed_*` scribbled into state.json while an instance is still rented is a
    claim, not a teardown -- and it is BURNING money. Stays stranded, stays stale.
    """
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-25T17:05:56Z", "run": "spender", "event": "run_opened"},
        {"ts": "2026-07-25T18:00:00Z", "run": "spender", "event": "phase_fire_done"},
    ])
    _state(tmp_path, "spender", result="killed_by_hand", instance_id=45996047,
           destroyed=False)
    got = wr.open_runs(p, now=now)
    assert [r["outcome"] for r in got] == ["stranded"]
    assert got[0]["stale"] is True
    assert wr.check_open_runs(p) == 1


def test_a_destroyed_instance_releases_the_spend_and_may_be_excused(wr, tmp_path):
    """Same run, once the instance is provably released (I4 satisfied)."""
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-25T17:05:56Z", "run": "torn_down", "event": "run_opened"},
        {"ts": "2026-07-25T18:00:00Z", "run": "torn_down", "event": "phase_fire_done"},
    ])
    _state(tmp_path, "torn_down", result="aborted_wedge_guard", instance_id=45996047,
           destroyed=True)
    assert wr.open_runs(p, now=now) == []
    assert wr.check_open_runs(p) == 0


def test_result_ok_without_run_closed_is_still_stranded(wr, tmp_path):
    """`ok` is NOT an abandonment. A successful run closes via `run_closed`; one
    that did not is precisely the 7/19 shape this whole check was built for."""
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-19T00:30:24Z", "run": "20260719-003024", "event": "run_opened"},
        {"ts": "2026-07-19T05:17:31Z", "run": "20260719-003024",
         "event": "phase_destroy_done"},
    ])
    _state(tmp_path, "20260719-003024", result="ok", destroyed=True,
           instance_id=1234, imported_servers=32545)
    got = wr.open_runs(p, now=now)
    assert [r["outcome"] for r in got] == ["stranded"] and got[0]["stale"] is True
    assert wr.check_open_runs(p) == 1


def test_missing_or_torn_state_is_not_evidence_of_abandonment(wr, tmp_path):
    """No state, unreadable state, or a state with no `result` all mean the same
    thing: nobody recorded a teardown, so the run stays stranded."""
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    p = _ledger(tmp_path, [
        {"ts": "2026-07-25T17:05:56Z", "run": "no_state", "event": "run_opened"},
        {"ts": "2026-07-25T17:05:56Z", "run": "torn", "event": "run_opened"},
        {"ts": "2026-07-25T17:05:56Z", "run": "no_result", "event": "run_opened"},
    ])
    (tmp_path / "torn").mkdir()
    (tmp_path / "torn" / "state.json").write_text('{"run_id": "torn", "resu',
                                                  encoding="utf-8")
    _state(tmp_path, "no_result", phases={"preflight": "done"})
    got = wr.open_runs(p, now=now)
    assert sorted(r["run_id"] for r in got) == ["no_result", "no_state", "torn"]
    assert all(r["stale"] for r in got)
    assert wr.check_open_runs(p) == 1


def test_reconciliation_reads_the_tree_the_caller_is_auditing(wr, tmp_path):
    """A ledger handed in explicitly must not be reconciled against the LIVE run
    tree -- the guard has to look at the runs sitting beside that ledger."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    p = _ledger(other, [
        {"ts": "2026-07-25T17:05:56Z", "run": "r1", "event": "run_opened"}])
    _state(tmp_path, "r1", result="killed_somewhere_else")   # wrong tree
    now = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
    assert [r["outcome"] for r in wr.open_runs(p, now=now)] == ["stranded"]
    # ... and honours an explicit override.
    assert wr.open_runs(p, now=now, runs_root=tmp_path) == []


# --------------------------------------------- FU-132: postcheck keeps its number
def _postcheck_run(wr, tmp_path, **state):
    run = wr.Run(tmp_path / "20260727-105859")
    run.state = {"run_id": "20260727-105859", "mode": "delta", "phases": {},
                 "baseline_freshness": {"scored_servers": 279111,
                                        "scores_rows": 1953777}, **state}
    run.dir.mkdir(parents=True, exist_ok=True)
    return run


def test_degraded_postcheck_keeps_the_number_it_already_had(wr, tmp_path, monkeypatch):
    """Run 20260727-105859 shipped `scored_servers.after: null` while its own
    state.json held 279116 from the import phase's direct DB read -- the very
    number I1 is enforced against. A report may be degraded; it must not be
    emptier than the state that produced it."""
    monkeypatch.setattr(wr, "freshness_safe",
                        lambda: ({}, "RuntimeError: freshness unreachable"))
    monkeypatch.setattr(wr, "ledger", lambda *a, **kw: None)
    run = _postcheck_run(wr, tmp_path, scored_after=279116, imported_servers=140005,
                         exported=140005, coverage=1.0, degraded=False, destroyed=True)
    wr.ph_postcheck(run, None)
    rep = json.loads((run.dir / "report.json").read_text())
    assert rep["degraded_postcheck"] is True
    assert rep["scored_servers"]["after"] == 279116
    assert rep["scored_servers"]["after_basis"] == "db_import"


def test_postcheck_prefers_the_freshness_surface_when_it_answers(wr, tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr(wr, "freshness_safe",
                        lambda: ({"scored_servers": 279116,
                                  "scores_rows": 1953812,
                                  "newest_scored_at": "2026-07-27T16:52:57"}, ""))
    monkeypatch.setattr(wr, "ledger", lambda *a, **kw: None)
    run = _postcheck_run(wr, tmp_path, scored_after=279116, destroyed=True)
    wr.ph_postcheck(run, None)
    rep = json.loads((run.dir / "report.json").read_text())
    assert rep["degraded_postcheck"] is False
    assert rep["scored_servers"]["after_basis"] == "freshness"
    assert rep["scores_rows"]["after"] == 1953812


def test_postcheck_with_neither_source_says_so_rather_than_guessing(wr, tmp_path,
                                                                    monkeypatch):
    """A run that failed before import has no DB number either. `after: null` is
    then the honest answer -- and the basis must say there was none."""
    monkeypatch.setattr(wr, "freshness_safe", lambda: ({}, "unreachable"))
    monkeypatch.setattr(wr, "ledger", lambda *a, **kw: None)
    run = _postcheck_run(wr, tmp_path, destroyed=True)
    wr.ph_postcheck(run, None)
    rep = json.loads((run.dir / "report.json").read_text())
    assert rep["scored_servers"]["after"] is None
    assert rep["scored_servers"]["after_basis"] is None


# ------------------------------------------------------------------ FU-133
# ensure_proxy() ran the command that explains the failure and threw its output
# away, so a 2026-07-28 preflight abort said "did not come up in 60s" when flyctl
# had plainly said "no access token available".
class _FakeProc:
    """A flyctl that writes to the stderr file it was handed, then exits."""

    def __init__(self, says: str = "", rc: int | None = 1, stderr=None):
        self._rc = rc
        if says and stderr is not None:
            stderr.write(says)
            stderr.flush()

    def poll(self):
        return self._rc


def _no_listener(wr, monkeypatch):
    """Every connect attempt fails -- the proxy never comes up."""
    class _Sock:
        def settimeout(self, *_a):
            pass

        def connect(self, *_a):
            raise OSError("refused")

        def close(self):
            pass

    monkeypatch.setattr(wr.socket, "socket", lambda *a, **kw: _Sock())
    monkeypatch.setattr(wr.time, "sleep", lambda *_a: None)


def test_proxy_failure_names_flyctls_own_reason(wr, tmp_path, monkeypatch):
    """The live 2026-07-28 shape: flyctl exits 1 with a one-line explanation."""
    monkeypatch.setattr(wr, "RUNS_ROOT", tmp_path)
    _no_listener(wr, monkeypatch)
    monkeypatch.setattr(wr.subprocess, "Popen", lambda *a, **kw: _FakeProc(
        "Error: no access token available. Please login with 'flyctl auth login'\n",
        rc=1, stderr=kw.get("stderr")))
    with pytest.raises(RuntimeError) as e:
        wr.ensure_proxy()
    msg = str(e.value)
    assert "no access token available" in msg
    assert "flyctl exited 1" in msg


def test_proxy_failure_still_reports_when_flyctl_says_nothing(wr, tmp_path,
                                                              monkeypatch):
    """Silence is a valid outcome; the check must not crash trying to explain it."""
    monkeypatch.setattr(wr, "RUNS_ROOT", tmp_path)
    _no_listener(wr, monkeypatch)
    monkeypatch.setattr(wr.subprocess, "Popen",
                        lambda *a, **kw: _FakeProc("", rc=None, stderr=kw.get("stderr")))
    with pytest.raises(RuntimeError, match="did not come up in 60s"):
        wr.ensure_proxy()


def test_a_dead_flyctl_is_not_waited_out(wr, tmp_path, monkeypatch):
    """It exited on attempt one. Sleeping through the remaining 29 polls delays the
    only useful signal by a minute and teaches the reader nothing."""
    monkeypatch.setattr(wr, "RUNS_ROOT", tmp_path)
    _no_listener(wr, monkeypatch)
    slept = {"n": 0}

    def counting_sleep(*_a):
        slept["n"] += 1

    monkeypatch.setattr(wr.time, "sleep", counting_sleep)
    monkeypatch.setattr(wr.subprocess, "Popen", lambda *a, **kw: _FakeProc(
        "boom\n", rc=2, stderr=kw.get("stderr")))
    with pytest.raises(RuntimeError):
        wr.ensure_proxy()
    assert slept["n"] == 1


def test_a_live_proxy_short_circuits_before_spawning_anything(wr, monkeypatch):
    """An already-listening proxy must be reused, not duplicated onto a taken port."""
    class _Sock:
        def settimeout(self, *_a):
            pass

        def connect(self, *_a):
            return None

        def close(self):
            pass

    monkeypatch.setattr(wr.socket, "socket", lambda *a, **kw: _Sock())

    def boom(*a, **kw):
        raise AssertionError("spawned a second proxy over a live one")

    monkeypatch.setattr(wr.subprocess, "Popen", boom)
    assert wr.ensure_proxy() is None

# ------------------------------------------------------------------ 2026-08-31
def test_a_terminal_failure_verdict_with_released_spend_is_not_stranded(wr, tmp_path):
    """Live shape 20260822-220319: deadline death, destroyed, skipped as terminal.

    `open_run` refused to resume it (`run_skipped_terminal`, via
    _terminally_finished) while this detector called it STRANDED forever --
    "deadline" is not an abandonment prefix and `run_skipped_terminal` is not
    an abort event. Two instruments disagreeing about one word, mirrored from
    FU-321. Terminal-with-spend-released must be excused like an abort;
    terminal-with-instance-still-held must stay stranded (the bar is not
    lowered).
    """
    now = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)
    rid = "20260822-220319"
    (tmp_path / rid).mkdir()
    state = {"run_id": rid, "result": "deadline",
             "instance_id": 48426884, "destroyed": True}
    (tmp_path / rid / "state.json").write_text(json.dumps(state), encoding="utf-8")
    p = _ledger(tmp_path, [
        {"ts": _ts(now - timedelta(hours=198)), "run": rid, "event": "run_opened"},
        {"ts": _ts(now - timedelta(hours=194)), "run": rid, "event": "phase_destroy_done"},
        {"ts": _ts(now - timedelta(hours=1)), "run": rid, "event": "run_skipped_terminal"},
    ])
    assert wr.open_runs(p, now=now) == []
    excused = wr.open_runs(p, now=now, include_aborted=True)
    assert excused and excused[0]["outcome"] == "aborted"

    # negative control: same verdict, instance NOT destroyed -> still stranded
    state["destroyed"] = False
    (tmp_path / rid / "state.json").write_text(json.dumps(state), encoding="utf-8")
    still = wr.open_runs(p, now=now)
    assert still and still[0]["outcome"] == "stranded" and still[0]["stale"]
