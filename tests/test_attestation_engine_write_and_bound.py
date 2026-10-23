"""Negative controls for attestation_engine's WRITE and its READ bound.

WHY THIS FILE EXISTS -- improvement-loop cycle-0176, 2026-10-04, gh#4003
------------------------------------------------------------------------
Two defects, both measured on the LIVE runtime before a line was changed.

1. THE WRITE. `mcp_attestations.confidence_level` is FLOAT on the live bus
   (information_schema, 2026-10-04). The engine wrote the VARCHAR band
   ('HIGH'/'MEDIUM'/'LOW') into it, so every write returned 500
   `Conversion Error: Could not convert string 'LOW' to FLOAT` -- verbatim from
   /home/workspace/logs/write_service.log -- from 2026-06-09 until 2026-10-04.
   117 days during which a LIVE daemon logged "Cycle complete. Generated 0
   attestations" at INFO every six hours and nothing noticed. The table holds
   28 rows, all generated inside one 13-minute window on 2026-06-09, all
   expired on 2026-07-09: ZERO valid attestations for the whole fleet.

2. THE READ BOUND. `get_all_servers_needing_attestation()` read a 3630-row
   table through a bus that caps a page at 200 rows. Live: `count=200
   truncated=true limit=200` against a derived `count(*)` of 3630 -- the engine
   asked which servers need attesting and was handed 5.5% of the answer with no
   way to tell. That is gh#4003's class, and bus.query_complete() is its door.

R4 -- A NEGATIVE CONTROL, OR IT IS NOT EVIDENCE. Every green assertion here is
paired with a pole that is RED. The doubles are typed the way the live bus is
typed, so the pre-cure payload is REJECTED by them: the `test_*_pre_cure_*`
tests reproduce the live failure against the double before any cure is asserted
to work. Delete them and this file stops being evidence of anything.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The live column types of mcp_attestations, read from information_schema on
# the running bus 2026-10-04. Not a guess and not this module's CREATE TABLE.
LIVE_ATTESTATION_COLUMNS = {
    "attestation_id": "VARCHAR",
    "server_id": "VARCHAR",
    "attestation_text": "VARCHAR",
    "scope": "VARCHAR",
    "confidence_level": "FLOAT",
    "valid_until": "TIMESTAMP WITH TIME ZONE",
    "risk_tier": "VARCHAR",
    "caveats": "VARCHAR",
    "status": "VARCHAR",
    "generated_at": "TIMESTAMP WITH TIME ZONE",
}

# The server cap, as bus.py records it from the live measurement.
CAP = 200

_LIMIT_OFFSET = re.compile(r"\bLIMIT\s+(\d+)\s+OFFSET\s+(\d+)\s*$", re.IGNORECASE)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(
                "%d Server Error: %s" % (self.status_code, self._payload)
            )


class FakeBus:
    """A write_service double that is TYPED, and that CAPS like the real one.

    It rejects a value its column cannot hold with the same 500 the live
    service returns, and it truncates an unbounded page at `cap` and declares
    `truncated=true` exactly as the bus has since #3997 shipped (2026-09-28).
    """

    def __init__(self, population, columns=None, cap=CAP, declare_truncation=True):
        self.population = list(population)
        self.columns = dict(columns or LIVE_ATTESTATION_COLUMNS)
        self.cap = cap
        self.declare_truncation = declare_truncation
        self.writes = []
        self.write_failures = []
        self.query_log = []

    # -- the typed write -------------------------------------------------
    def _write(self, body):
        row = body.get("rows") or {}
        for key, value in row.items():
            declared = self.columns.get(key)
            if declared is None:
                err = "Binder Error: column \"%s\" not found" % key
                self.write_failures.append(err)
                return FakeResponse(500, {"detail": err})
            if declared in ("FLOAT", "DOUBLE", "INTEGER", "BIGINT"):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    try:
                        float(value)
                    except (TypeError, ValueError):
                        err = ("Conversion Error: Could not convert string '%s' "
                               "to FLOAT" % value)
                        self.write_failures.append(err)
                        return FakeResponse(500, {"detail": err})
        self.writes.append(dict(row))
        return FakeResponse(200, {"status": "ok", "written": 1})

    # -- the capping read ------------------------------------------------
    def _query(self, body):
        sql = body.get("sql", "")
        self.query_log.append(sql)

        if "information_schema.columns" in sql:
            rows = [{"column_name": name, "data_type": dtype}
                    for name, dtype in self.columns.items()]
            return FakeResponse(200, {"rows": rows, "count": len(rows),
                                      "truncated": False})

        if "_zo_paged_sub" in sql and "count(*)" in sql:
            return FakeResponse(200, {"rows": [{"n": len(self.population)}],
                                      "count": 1, "truncated": False})

        rows = list(self.population)
        match = _LIMIT_OFFSET.search(sql)
        if match:
            limit, offset = int(match.group(1)), int(match.group(2))
            rows = rows[offset:offset + limit]
        payload = {}
        if len(rows) > self.cap:
            rows = rows[: self.cap]
            if self.declare_truncation:
                payload["truncated"] = True
                payload["limit"] = self.cap
        elif self.declare_truncation:
            payload["truncated"] = False
        payload["rows"] = rows
        payload["count"] = len(rows)
        return FakeResponse(200, payload)

    # -- requests.post ---------------------------------------------------
    def post(self, url, json=None, timeout=None, **_kwargs):
        body = json or {}
        if url.endswith("/query"):
            return self._query(body)
        if url.endswith("/write"):
            return self._write(body)
        return FakeResponse(200, {"status": "ok"})


def _registry_rows(n):
    return [
        {
            "server_id": "srv-%04d" % i,
            "name": "server-%04d" % i,
            "verdict": "TRUSTED_GENERAL",
            "trust_score": 80.0,
            "confidence": 0.42,
        }
        for i in range(n)
    ]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Import attestation_engine with its host paths redirected at the tmp dir.

    Before this cycle the module could not be imported off the tower at all:
    `os.makedirs('/home/workspace/zo_sentinel/logs')` ran at import time and a
    FileHandler was built on it unconditionally. That is why a daemon with a
    117-day-old write failure had no test.
    """
    monkeypatch.setenv("ZO_ATTESTATION_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ZO_ATTESTATION_REPORT_PATH",
                       str(tmp_path / "ATTESTATION_REPORT.md"))
    sys.modules.pop("attestation_engine", None)
    mod = importlib.import_module("attestation_engine")
    # getattr, not attribute access: against the PRE-cure module this attribute
    # does not exist, and the fixture must not be what fails. Each test has to
    # fail on its OWN assertion for the negative control to mean anything (R4).
    getattr(mod, "_COLUMN_TYPES", {}).clear()
    return mod


def _wire(monkeypatch, engine, bus):
    """Point both the module and the bus door at the double."""
    monkeypatch.setattr(engine.requests, "post", bus.post)
    bus_mod = sys.modules.get("zo_sentinel.bus")
    if bus_mod is not None:
        monkeypatch.setattr(bus_mod.requests, "post", bus.post)


# ---------------------------------------------------------------------------
# 1. THE WRITE -- the 117-day defect, reproduced then cured
# ---------------------------------------------------------------------------

def test_pre_cure_the_band_string_is_REJECTED_by_a_float_column(engine, monkeypatch):
    """RED POLE. The payload this module sent until 2026-10-04 must FAIL.

    If this ever passes, the double has stopped being typed like the live bus
    and every green assertion below is worthless.
    """
    bus = FakeBus(population=[], columns=LIVE_ATTESTATION_COLUMNS)
    _wire(monkeypatch, engine, bus)

    pre_cure_row = {"server_id": "srv-0001", "confidence_level": "LOW"}
    resp = bus.post(engine.WRITE_SERVICE_URL, json={
        "table": "mcp_attestations", "rows": pre_cure_row, "wait": True})

    assert resp.status_code == 500
    assert "Could not convert string 'LOW' to FLOAT" in json.dumps(resp.json())
    assert bus.writes == []


def test_the_cured_attestation_is_accepted_by_the_live_float_column(
        engine, monkeypatch):
    bus = FakeBus(population=_registry_rows(1), columns=LIVE_ATTESTATION_COLUMNS)
    _wire(monkeypatch, engine, bus)

    monkeypatch.setattr(engine, "fetch_server_data", lambda sid: {
        "server_id": sid, "name": "server-0000", "trust_score": 80.0,
        "verdict": "TRUSTED_GENERAL", "confidence": 0.42, "risk_tier": None,
        "last_assessed": None})
    monkeypatch.setattr(engine, "fetch_risk_tier", lambda sid: "LOW")

    attestation = engine.generate_attestation("srv-0000")
    assert isinstance(attestation["confidence_level"], float), (
        "the FLOAT column must receive the number, not the band")
    assert attestation["confidence_level"] == pytest.approx(0.42)

    engine.write_attestation(attestation)
    assert bus.write_failures == [], bus.write_failures
    assert len(bus.writes) == 1
    assert bus.writes[0]["confidence_level"] == pytest.approx(0.42)


def test_a_VARCHAR_column_gets_the_band_instead(engine, monkeypatch):
    """The branch is chosen by the LIVE type (R1), not by a belief in this file.

    So the same code is correct against a bus whose column is VARCHAR -- which
    is what this module's own CREATE TABLE has always declared.
    """
    columns = dict(LIVE_ATTESTATION_COLUMNS, confidence_level="VARCHAR")
    bus = FakeBus(population=[], columns=columns)
    _wire(monkeypatch, engine, bus)

    assert engine.confidence_level_for_column(0.42, "LOW") == "LOW"
    assert engine.confidence_level_for_column(0.90, "HIGH") == "HIGH"


def test_an_unknown_column_type_is_not_read_as_absent(engine, monkeypatch):
    """R6. A failed information_schema read must not be cached as "no column".

    It falls back to the type MEASURED live, and the next call re-resolves.
    """
    class Broken(FakeBus):
        def _query(self, body):
            if "information_schema.columns" in body.get("sql", ""):
                return FakeResponse(500, {"detail": "bus down"})
            return super()._query(body)

    bus = Broken(population=[], columns=LIVE_ATTESTATION_COLUMNS)
    _wire(monkeypatch, engine, bus)

    assert engine.column_types("mcp_attestations") == {}
    assert "mcp_attestations" not in engine._COLUMN_TYPES
    assert engine.confidence_level_for_column(0.42, "LOW") == pytest.approx(0.42)


def test_the_band_itself_still_derives_from_the_number(engine):
    assert engine.confidence_band_of(0.90) == "HIGH"
    assert engine.confidence_band_of(0.85) == "HIGH"
    assert engine.confidence_band_of(0.60) == "MEDIUM"
    assert engine.confidence_band_of(0.59) == "LOW"
    assert engine.confidence_band_of(None) == "LOW"


# ---------------------------------------------------------------------------
# 2. THE READ BOUND -- gh#4003, reproduced then cured
# ---------------------------------------------------------------------------

def test_pre_cure_an_unbounded_read_of_an_over_cap_table_returns_only_the_cap(
        engine, monkeypatch):
    """RED POLE. The single un-paginated POST, exactly as it was written.

    470 rows in, 200 out, `truncated=true` on the wire and the old helper threw
    it away. This is gh#4003 in four lines, and it must keep failing here.
    """
    bus = FakeBus(population=_registry_rows(470))
    _wire(monkeypatch, engine, bus)

    body = bus.post(engine.QUERY_URL, json={"sql": "SELECT * FROM r"}).json()

    assert body["count"] == CAP
    assert body["truncated"] is True
    assert len(body["rows"]) == CAP < 470


def test_the_cured_ws_query_returns_every_row(engine, monkeypatch):
    bus = FakeBus(population=_registry_rows(470))
    _wire(monkeypatch, engine, bus)

    rows = engine.ws_query("SELECT server_id, name FROM mcp_server_registry")

    assert len(rows) == 470, (
        "the whole relation, not the first page: got %d" % len(rows))
    ids = [r["server_id"] for r in rows]
    assert len(set(ids)) == 470, "a LIMIT/OFFSET walk duplicated rows"
    assert ids[0] == "srv-0000" and ids[-1] == "srv-0469"


def test_get_all_servers_needing_attestation_sees_the_whole_fleet(
        engine, monkeypatch):
    """The live shape: 3630 candidates, a 200-row cap, one caller."""
    bus = FakeBus(population=_registry_rows(3630))
    _wire(monkeypatch, engine, bus)

    servers = engine.get_all_servers_needing_attestation()

    assert len(servers) == 3630
    assert len(servers) > CAP, "the whole point of gh#4003"


def test_a_bus_that_declares_nothing_is_still_not_trusted_to_be_complete(
        engine, monkeypatch):
    """R6 again: a pre-#3997 bus says nothing about truncation.

    Absence of the flag is UNKNOWN, so a full first page must be paged with
    reconciliation rather than returned as the whole answer.
    """
    bus = FakeBus(population=_registry_rows(470), declare_truncation=False)
    _wire(monkeypatch, engine, bus)

    rows = engine.ws_query("SELECT server_id, name FROM mcp_server_registry")
    assert len(rows) == 470


def test_a_read_failure_does_not_become_an_empty_fleet(engine, monkeypatch):
    """R6, the version that cost the most.

    `except: return []` told cycle() that NO server needed attestation, which
    is byte-identical to a fully-attested fleet. It must raise.
    """
    class Down(FakeBus):
        def _query(self, body):
            return FakeResponse(503, {"detail": "write_service restarting"})

    bus = Down(population=_registry_rows(10))
    _wire(monkeypatch, engine, bus)

    with pytest.raises(Exception):
        engine.get_all_servers_needing_attestation()


# ---------------------------------------------------------------------------
# 3. THE SILENCE -- a cycle that attempted work and produced nothing
# ---------------------------------------------------------------------------

def test_a_cycle_that_writes_nothing_is_logged_as_an_ERROR(
        engine, monkeypatch, caplog):
    """The line that would have surfaced the FLOAT mismatch on day one.

    Pre-cure, this condition was `logger.info("... Generated 0 attestations")`
    and nothing else, for 117 days.
    """
    bus = FakeBus(population=_registry_rows(5))
    _wire(monkeypatch, engine, bus)
    monkeypatch.setattr(engine, "create_attestations_table", lambda: None)
    monkeypatch.setattr(engine, "generate_attestation",
                        lambda sid: {"server_id": sid,
                                     "confidence_level": "LOW"})

    with caplog.at_level(logging.ERROR, logger=engine.SERVICE_NAME):
        written = engine.cycle()

    assert written == 0
    loud = [r for r in caplog.records
            if "ATTESTATION CYCLE PRODUCED NOTHING" in r.getMessage()]
    assert len(loud) == 1, "attempted>0 and written=0 must be loud"
    assert "attempted=5" in loud[0].getMessage()
    assert "Could not convert string 'LOW' to FLOAT" in loud[0].getMessage()


def test_a_cycle_that_writes_everything_stays_quiet(engine, monkeypatch, caplog):
    """OVER-FIRING CONTROL, the dangerous direction.

    A signal that fires on a healthy cycle is noise, and noise is how the next
    real failure gets ignored. This pole must stay silent.
    """
    bus = FakeBus(population=_registry_rows(3))
    _wire(monkeypatch, engine, bus)
    monkeypatch.setattr(engine, "create_attestations_table", lambda: None)
    monkeypatch.setattr(engine, "generate_report", lambda rows: None)
    monkeypatch.setattr(engine, "generate_attestation",
                        lambda sid: {"server_id": sid, "confidence_level": 0.42})

    with caplog.at_level(logging.ERROR, logger=engine.SERVICE_NAME):
        written = engine.cycle()

    assert written == 3
    assert bus.write_failures == []
    assert not [r for r in caplog.records
                if "ATTESTATION CYCLE PRODUCED NOTHING" in r.getMessage()]


def test_an_empty_backlog_stays_quiet_too(engine, monkeypatch, caplog):
    """Nothing to do is not a failure, so it must not be logged as one."""
    bus = FakeBus(population=[])
    _wire(monkeypatch, engine, bus)
    monkeypatch.setattr(engine, "create_attestations_table", lambda: None)

    with caplog.at_level(logging.ERROR, logger=engine.SERVICE_NAME):
        written = engine.cycle()

    assert written == 0
    assert not [r for r in caplog.records
                if "ATTESTATION CYCLE PRODUCED NOTHING" in r.getMessage()]
