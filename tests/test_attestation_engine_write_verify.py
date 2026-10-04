"""Negative controls for "acknowledged is not stored".

WHY THIS FILE EXISTS -- improvement-loop cycle-0179, 2026-10-04, gh#4003
------------------------------------------------------------------------
cycle-0176 cured the two defects that kept this daemon at zero attestations:
the FLOAT/VARCHAR write and the 200-row read cap. Both armed. Measured on the
live runtime today, the cure worked exactly as designed:

    10:50:11  Starting attestation cycle
    10:51:02  Found 3637 servers needing attestation      <- was 200 (the cap)
    11:49:36  Cycle complete. Generated 1062 attestations <- was 0

And then, on the same bus:

    18:25  SELECT count(*) FROM mcp_attestations     ->  28
           SELECT max(generated_at) ...              ->  2026-06-09T00:29:33
           count(DISTINCT server_id) valid_until>now ->  0

Not one of the 1062 is in the table. The mechanism, from
/home/workspace/logs/write_service.log verbatim:

    [2026-10-04 11:49:33] [write_wrapper] Starting WriteService (attempt 1)
    [2026-10-04 12:07:22] [write_wrapper] Stale WAL (1072s old) -- removing

write_service acknowledges a write with {"ok":true,"queued":1,"wait":true} --
an enqueue receipt. The service was restarted mid-batch and its wrapper then
deleted the un-checkpointed WAL as stale. 1072s before 12:07:22 is 11:49:30:
that WAL was this cycle's batch.

THE DEFECT THIS FILE PINS IS THE ENGINE'S, NOT THE WRAPPER'S. write_service is
NEVER_TOUCH for this lane and the WAL-removal path is reported upstream. What
belongs here is that the engine counted 2xx responses, called them
attestations, reported 1062, and slept six hours over a fleet that had gained
nothing. A daemon must re-derive its own output from the store before it
reports it -- R3 (a bucket must prove the check ran), R6 (unknown is not zero)
and R5 (publish the basis).

R4 -- A NEGATIVE CONTROL, OR IT IS NOT EVIDENCE. Every pole here has a red
twin, including in the dangerous direction: a bus that cannot answer the
verification query must NOT be read as data loss, and a healthy cycle must
stay silent. Against the pre-cure module the acknowledged-but-not-stored poles
and the backoff pole FAIL on their own assertions; that failure is the
evidence, and deleting them deletes it.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from test_attestation_engine_write_and_bound import (  # noqa: E402
    FakeBus, FakeResponse, _registry_rows, _wire,
)

VERIFY_MARKER = "count(*) AS n FROM mcp_attestations"


class StoringBus(FakeBus):
    """A bus that stores what it acknowledges, and can be asked how many.

    `mode` picks what the verification query does, which is the whole point of
    the file:
      'store'   -- answers from the rows it actually kept (healthy)
      'lose'    -- acknowledges every write, keeps none (the 2026-10-04 WAL)
      'partial' -- keeps the first `keep` writes only
      'down'    -- the verification read itself fails (UNKNOWN, not zero)
      'garbage' -- answers with a body that carries no usable count
    """

    def __init__(self, population, mode="store", keep=0, **kwargs):
        super().__init__(population, **kwargs)
        self.mode = mode
        self.keep = keep
        self.verify_queries = []

    def _stored_count(self):
        if self.mode == "lose":
            return 0
        if self.mode == "partial":
            return min(self.keep, len(self.writes))
        return len(self.writes)

    def _query(self, body):
        sql = body.get("sql", "")
        if VERIFY_MARKER in sql:
            self.verify_queries.append(sql)
            if self.mode == "down":
                raise RuntimeError("Connection reset by peer")
            if self.mode == "garbage":
                return FakeResponse(200, {"rows": [], "count": 0})
            return FakeResponse(200, {"rows": [{"n": self._stored_count()}],
                                      "count": 1, "truncated": False})
        return super()._query(body)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("ZO_ATTESTATION_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ZO_ATTESTATION_REPORT_PATH",
                       str(tmp_path / "ATTESTATION_REPORT.md"))
    sys.modules.pop("attestation_engine", None)
    mod = importlib.import_module("attestation_engine")
    getattr(mod, "_COLUMN_TYPES", {}).clear()
    return mod


def _arm(monkeypatch, engine, bus):
    _wire(monkeypatch, engine, bus)
    monkeypatch.setattr(engine, "create_attestations_table", lambda: None)
    monkeypatch.setattr(engine, "generate_report", lambda rows: None)
    monkeypatch.setattr(
        engine, "generate_attestation",
        lambda sid: {"server_id": sid, "confidence_level": 0.42})


# ---------------------------------------------------------------------------
# 1. The 2026-10-04 loss, reproduced
# ---------------------------------------------------------------------------

def test_acknowledged_but_not_stored_is_a_FAILED_cycle(engine, monkeypatch):
    """THE RED POLE. 1062 acknowledged, 0 stored, reported as success.

    Pre-cure this returned 5 and logged "Generated 5 attestations". The table
    had gained nothing and no caller could tell.
    """
    bus = StoringBus(population=_registry_rows(5), mode="lose")
    _arm(monkeypatch, engine, bus)

    with pytest.raises(engine.AttestationWritesLost) as excinfo:
        engine.cycle()

    assert len(bus.writes) == 5, "the writes were acknowledged"
    assert bus.verify_queries, "the store must be re-read, not trusted"
    msg = str(excinfo.value)
    assert "acknowledged 5" in msg and "holds 0" in msg


def test_the_verification_statement_is_an_aggregate_the_cap_cannot_reach(
        engine, monkeypatch):
    """gh#4003's own defect must not be reintroduced by its own check.

    A row-returning verification read would itself be capped at 200, so a
    cycle that wrote 1062 would "verify" 200 and report a 862-row loss that
    never happened. The statement is an aggregate, so a cap of 1 cannot touch
    the number it returns.
    """
    bus = StoringBus(population=[], mode="store", cap=1)
    bus.writes = [{"server_id": "srv-%04d" % i} for i in range(1062)]
    _wire(monkeypatch, engine, bus)

    assert engine.count_attestations_since("2026-10-04T00:00:00+00:00") == 1062
    sql = bus.verify_queries[-1]
    assert "count(*)" in sql.lower()
    assert "limit" not in sql.lower(), "a verification read must not be paged"


# ---------------------------------------------------------------------------
# 2. The dangerous direction -- do not invent a loss
# ---------------------------------------------------------------------------

def test_a_healthy_cycle_stays_quiet_and_returns_its_count(
        engine, monkeypatch, caplog):
    """OVER-FIRING CONTROL. A check that fires on a good cycle is noise."""
    bus = StoringBus(population=_registry_rows(4), mode="store")
    _arm(monkeypatch, engine, bus)

    with caplog.at_level(logging.ERROR, logger=engine.SERVICE_NAME):
        assert engine.cycle() == 4

    assert not [r for r in caplog.records if "LOST" in r.getMessage()]
    assert not [r for r in caplog.records
                if "PRODUCED NOTHING" in r.getMessage()]


def test_a_verification_read_that_FAILS_is_UNKNOWN_not_a_loss(
        engine, monkeypatch, caplog):
    """R6. The blip that would have faked a data-loss alarm.

    write_service restarted four times on 2026-10-04. If an unanswerable
    verification read counted as zero, every one of those restarts would have
    raised AttestationWritesLost over writes that were fine.
    """
    bus = StoringBus(population=_registry_rows(3), mode="down")
    _arm(monkeypatch, engine, bus)

    with caplog.at_level(logging.WARNING, logger=engine.SERVICE_NAME):
        assert engine.cycle() == 3  # no raise

    said = " ".join(r.getMessage() for r in caplog.records)
    assert "UNKNOWN, not zero" in said
    assert "LOST" not in said


def test_a_verification_body_with_no_count_is_also_UNKNOWN(
        engine, monkeypatch, caplog):
    bus = StoringBus(population=_registry_rows(2), mode="garbage")
    _arm(monkeypatch, engine, bus)

    with caplog.at_level(logging.WARNING, logger=engine.SERVICE_NAME):
        assert engine.cycle() == 2

    assert "UNKNOWN, not zero" in " ".join(
        r.getMessage() for r in caplog.records)


def test_partial_loss_is_LOUD_but_not_fatal(engine, monkeypatch, caplog):
    """Some rows landed, so the queue drains; say so and keep going."""
    bus = StoringBus(population=_registry_rows(5), mode="partial", keep=2)
    _arm(monkeypatch, engine, bus)

    with caplog.at_level(logging.ERROR, logger=engine.SERVICE_NAME):
        assert engine.cycle() == 5  # no raise

    loud = [r for r in caplog.records
            if "ATTESTATION WRITES PARTIALLY LOST" in r.getMessage()]
    assert len(loud) == 1
    assert "acknowledged=5 stored=2" in loud[0].getMessage()


def test_an_empty_backlog_does_not_even_verify(engine, monkeypatch):
    """Nothing written, nothing to reconcile, no extra read on the bus."""
    bus = StoringBus(population=[], mode="lose")
    _arm(monkeypatch, engine, bus)

    assert engine.cycle() == 0
    assert bus.verify_queries == []


# ---------------------------------------------------------------------------
# 3. The six hours a ten-second outage used to cost
# ---------------------------------------------------------------------------

class _StopLoop(Exception):
    pass


def _run_capturing_sleeps(engine, monkeypatch, cycle_side_effects):
    """Drive run() through len(cycle_side_effects) iterations, recording sleeps."""
    slept = []
    calls = {"n": 0}

    def fake_cycle():
        i = calls["n"]
        calls["n"] += 1
        effect = cycle_side_effects[i]
        if isinstance(effect, Exception):
            raise effect
        return effect

    def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= len(cycle_side_effects):
            raise _StopLoop

    monkeypatch.setattr(engine, "check_single_instance", lambda *a, **k: None)
    monkeypatch.setattr(engine, "send_heartbeat", lambda: None)
    monkeypatch.setattr(engine, "cycle", fake_cycle)
    monkeypatch.setattr(engine.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        engine.run()
    return slept


def test_a_failed_cycle_retries_soon_instead_of_sleeping_the_full_interval(
        engine, monkeypatch):
    """THE RED POLE. 2026-10-04 14:51:21 cost six hours for a 10s bus restart.

    Pre-cure run() slept CYCLE_INTERVAL unconditionally, so this list was
    [21600, 21600].
    """
    boom = RuntimeError("ConnectionError reading the bus: Connection reset")
    slept = _run_capturing_sleeps(engine, monkeypatch, [boom, boom])

    assert slept[0] == engine.RETRY_INTERVAL
    assert slept[0] < engine.CYCLE_INTERVAL
    assert slept[1] == min(engine.RETRY_INTERVAL * 2,
                           engine.RETRY_BACKOFF_MAX), "backoff must escalate"


def test_backoff_is_capped_and_a_good_cycle_resets_it(engine, monkeypatch):
    """A retry loop that never widens is a hot loop; one that never resets
    punishes the fleet for an outage that ended."""
    boom = RuntimeError("down")
    slept = _run_capturing_sleeps(engine, monkeypatch, [boom, 7, boom])

    assert slept[0] == engine.RETRY_INTERVAL
    assert slept[1] == engine.CYCLE_INTERVAL, "a good cycle sleeps the interval"
    assert slept[2] == engine.RETRY_INTERVAL, "and resets the backoff"
    assert engine.RETRY_BACKOFF_MAX <= engine.CYCLE_INTERVAL


def test_a_successful_cycle_still_sleeps_the_full_interval(engine, monkeypatch):
    """OVER-FIRING CONTROL: the backoff must not shorten a healthy loop."""
    slept = _run_capturing_sleeps(engine, monkeypatch, [3])
    assert slept == [engine.CYCLE_INTERVAL]
