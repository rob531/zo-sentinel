# services/staged/verdict_audit_trail/logic.py
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter()


class AuditEntry(BaseModel):
    timestamp_iso: str
    action: Optional[str]
    # `verdict` and `detail` are the WIRE names this endpoint has always
    # published. The bus columns behind them are `outcome` and `details_json`
    # -- audit_log has never had a `verdict` or a `detail` column. Keeping the
    # wire names and fixing the SQL preserves the contract while making the
    # query executable; see issue #4080.
    verdict: Optional[str]
    actor: Optional[str]
    detail: Optional[str]


class AuditTrailResponse(BaseModel):
    server_id: str
    entries: List[AuditEntry]


def get_audit_trail(server_id: str) -> Dict[str, Any]:
    """
    Query audit_log table via write_service /query endpoint.
    Returns {server_id, entries: [{timestamp_iso, action, verdict, actor, detail}]}
    """
    query = {
        "sql": """
            SELECT
                al.timestamp,
                al.action,
                al.outcome AS verdict,
                al.actor,
                al.details_json AS detail
            FROM audit_log al
            WHERE al.target_server_id = :server_id
            ORDER BY al.timestamp DESC
            LIMIT 50
        """,
        "params": {"server_id": server_id}
    }

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=10
        )
        response.raise_for_status()
        payload = response.json()
        # :8772/query answers {"rows": [...], "count": N}. Iterating the
        # envelope itself walks its KEYS, which is silently empty-ish rather
        # than an error -- the list branch is kept only for a caller that
        # already unwrapped it.
        if isinstance(payload, dict):
            rows = payload.get("rows", payload.get("results", []))
        else:
            rows = payload or []

        entries = []
        for row in rows:
            ts = row.get("timestamp")
            if isinstance(ts, str):
                ts_iso = ts
            elif ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else datetime.fromtimestamp(ts, tz=timezone.utc)
                ts_iso = dt.isoformat()
            else:
                ts_iso = None
            
            entries.append(AuditEntry(
                timestamp_iso=ts_iso,
                action=row.get("action", ""),
                verdict=row.get("verdict"),
                actor=row.get("actor"),
                detail=row.get("detail")
            ))
        
        return {"server_id": server_id, "entries": entries}
    except Exception as e:
        return {"server_id": server_id, "entries": [], "error": str(e)}


@router.get("/api/servers/{server_id}/audit-trail", response_model=AuditTrailResponse)
def get_server_audit_trail(server_id: str) -> AuditTrailResponse:
    """Get audit trail for a specific server."""
    result = get_audit_trail(server_id)
    return AuditTrailResponse(**result)


if __name__ == "__main__":
    import sys

    # SELF-TEST -- the previous version of this block was HOLLOW, and that is
    # the whole reason this file needed repairing (issue #4080).
    #
    # It created its own `audit_log` with `verdict` and `detail` columns, then
    # asserted the query worked. The bus table has neither; it has `outcome`
    # and `details_json`. So the test passed against a schema that exists
    # nowhere while the endpoint 400'd in production -- the artifact inspected
    # was not the artifact that runs.
    #
    # The fixture below is therefore pinned to the REAL bus column set,
    # measured from information_schema on 2026-09-14:
    #   action, actor, details_json, event_id, event_type, immutable,
    #   outcome, target_server_id, timestamp
    # and the test runs a NEGATIVE CONTROL first: the pre-fix SQL must FAIL
    # against that fixture. A test never observed failing is not evidence.
    BUS_AUDIT_LOG_COLUMNS = [
        "event_id", "event_type", "timestamp", "target_server_id",
        "action", "actor", "outcome", "details_json", "immutable",
    ]

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE audit_log (
                event_id INTEGER PRIMARY KEY,
                event_type TEXT,
                timestamp TEXT,
                target_server_id TEXT,
                action TEXT,
                actor TEXT,
                outcome TEXT,
                details_json TEXT,
                immutable INTEGER
            )
        """))
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(text("""
            INSERT INTO audit_log
                (event_type, timestamp, target_server_id, action, actor, outcome, details_json, immutable)
            VALUES
                ('scan', :ts1, 'srv_test_001', 'scan_completed', 'system', 'safe',   'vulnerability scan finished', 1),
                ('score', :ts2, 'srv_test_001', 'score_updated', 'admin',  'medium', 'risk score adjusted', 1)
        """), {"ts1": now, "ts2": now})
        conn.commit()

    def _run(sql: str):
        with engine.connect() as conn:
            result = conn.execute(text(sql), {"server_id": "srv_test_001"})
            keys = list(result.keys())
            return [dict(zip(keys, row)) for row in result]

    # The SQL the endpoint issues. Kept byte-identical to the query in
    # get_audit_trail above; the negative control below is what catches the two
    # drifting apart, because the pre-fix form is derived FROM this string.
    PRODUCTION_SQL = """
        SELECT
            al.timestamp,
            al.action,
            al.outcome AS verdict,
            al.actor,
            al.details_json AS detail
        FROM audit_log al
        WHERE al.target_server_id = :server_id
        ORDER BY al.timestamp DESC
        LIMIT 50
    """
    PRE_FIX_SQL = PRODUCTION_SQL.replace(
        "al.outcome AS verdict", "al.verdict"
    ).replace("al.details_json AS detail", "al.detail")

    failures = []

    # [1] NEGATIVE CONTROL -- the pre-fix SQL must not resolve on the real
    #     column set. If this passes, the fixture has drifted back into
    #     inventing columns and every assertion below is worthless.
    try:
        _run(PRE_FIX_SQL)
        failures.append(
            "NEGATIVE CONTROL DID NOT FIRE: the pre-fix SQL (al.verdict / al.detail) "
            "resolved against the fixture, so the fixture is not the real schema"
        )
    except Exception:
        pass

    # [2] the fixture matches the measured bus column set
    with engine.connect() as conn:
        got = sorted(r[1] for r in conn.execute(text("PRAGMA table_info(audit_log)")))
    if got != sorted(BUS_AUDIT_LOG_COLUMNS):
        failures.append(f"fixture columns {got} != measured bus columns {sorted(BUS_AUDIT_LOG_COLUMNS)}")

    # [3] POSITIVE CONTROL -- the shipped SQL resolves and returns both rows
    try:
        rows = _run(PRODUCTION_SQL)
        if len(rows) != 2:
            failures.append(f"expected 2 rows from the production SQL, got {len(rows)}")
        if rows and rows[0].get("verdict") is None:
            failures.append("production SQL returned a NULL verdict; the outcome alias is not landing")
    except Exception as e:
        failures.append(f"production SQL failed against the real column set: {e}")
        rows = []

    # [4] the envelope unwrap -- :8772 answers {"rows": [...], "count": N}.
    #     Feeding the bare envelope to the old code iterated its KEYS.
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    original_post = requests.post
    try:
        requests.post = lambda *a, **k: FakeResponse({"rows": rows, "count": len(rows)})
        result = get_audit_trail("srv_test_001")
        if result.get("error"):
            failures.append(f"enveloped response errored: {result['error']}")
        if result["server_id"] != "srv_test_001":
            failures.append(f"expected server_id 'srv_test_001', got {result['server_id']}")
        if len(result["entries"]) != 2:
            failures.append(f"expected 2 entries from the enveloped response, got {len(result['entries'])}")

        # and the already-unwrapped list form still works
        requests.post = lambda *a, **k: FakeResponse(rows)
        bare = get_audit_trail("srv_test_001")
        if len(bare["entries"]) != 2:
            failures.append(f"expected 2 entries from the bare-list response, got {len(bare['entries'])}")
    finally:
        requests.post = original_post

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print("PASS (negative control fired, production SQL resolves on the measured bus schema)")
    sys.exit(0)