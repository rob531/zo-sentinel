"""Integration tests: real payloads/queries through write_service -> real DuckDB.

These run against tests/integration/it_write_service.py (a REAL-DuckDB-backed
write_service started by the `integration` workflow), reached over HTTP at
$ZO_WRITE_SERVICE -- the same access pattern production daemons use. Unlike the
hermetic smoke-ladder (which uses a dict-mock with no SQL engine), these exercise
genuine SQL: write -> DuckDB -> query round-trip + real WHERE filtering + the
risk_tier write path the LLM cutover depends on.

When the app migrates to Postgres (docs/POSTGRES_APP_MIGRATION_SCOPE.md), the same
tests run unchanged against a Postgres-backed service (IT_DB_BACKEND=postgres).
"""
import os
import time

import requests

WS = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
T = 10


def _write(table, rows):
    r = requests.post(f"{WS}/write", json={"table": table, "rows": rows, "wait": True}, timeout=T)
    r.raise_for_status()
    return r.json()


def _query(sql):
    r = requests.post(f"{WS}/query", json={"sql": sql}, timeout=T)
    r.raise_for_status()
    return r.json().get("rows", [])


def _execute(sql):
    r = requests.post(f"{WS}/execute", json={"sql": sql}, timeout=T)
    r.raise_for_status()
    return r.json()


def _sid(tag):
    return f"it_{tag}_{int(time.time()*1e6)}"


def test_health():
    r = requests.get(f"{WS}/health", timeout=T)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("tables", 0) > 0  # schema actually loaded into a real engine


def test_registry_write_read_roundtrip():
    """A row written via /write MUST be queryable via /query (the #174 lesson:
    'ok' is not enough -- it has to actually land in the DB)."""
    sid = _sid("rt")
    res = _write("mcp_server_registry", [{
        "server_id": sid, "name": "it-roundtrip", "registry_source": "it",
        "description": "integration round-trip probe", "verdict": "TRUSTED_GENERAL",
        "trust_score": 81.0,
    }])
    assert res["ok"] and res["queued"] == 1
    rows = _query(f"SELECT server_id, name, verdict FROM mcp_server_registry WHERE server_id = '{sid}'")
    assert len(rows) == 1
    assert rows[0]["name"] == "it-roundtrip"
    assert rows[0]["verdict"] == "TRUSTED_GENERAL"


def test_real_sql_where_filter():
    """Genuine SQL filtering (the dict-mock only did regex). Verdict + numeric
    comparison must behave like a real engine."""
    a, b = _sid("filt_a"), _sid("filt_b")
    _write("mcp_server_registry", [
        {"server_id": a, "name": "lowrisk", "registry_source": "it", "verdict": "TRUSTED_GENERAL", "trust_score": 90.0},
        {"server_id": b, "name": "highrisk", "registry_source": "it", "verdict": "HIGH_RISK_ISOLATED", "trust_score": 12.0},
    ])
    hi = _query(f"SELECT server_id FROM mcp_server_registry WHERE verdict = 'HIGH_RISK_ISOLATED' AND server_id IN ('{a}','{b}')")
    assert [r["server_id"] for r in hi] == [b]
    low = _query(f"SELECT server_id FROM mcp_server_registry WHERE trust_score > 50 AND server_id IN ('{a}','{b}')")
    assert [r["server_id"] for r in low] == [a]


def test_risk_tier_update_path():
    """Exercises the registry.risk_tier write path the LLM-risk cutover uses:
    insert with no tier, then set it, then read it back."""
    sid = _sid("tier")
    _write("mcp_server_registry", [{"server_id": sid, "name": "tier-probe", "registry_source": "it"}])
    before = _query(f"SELECT risk_tier FROM mcp_server_registry WHERE server_id = '{sid}'")
    assert len(before) == 1 and (before[0]["risk_tier"] in (None, ""))
    _execute(f"UPDATE mcp_server_registry SET risk_tier = 'CRITICAL' WHERE server_id = '{sid}'")
    after = _query(f"SELECT risk_tier FROM mcp_server_registry WHERE server_id = '{sid}'")
    assert after[0]["risk_tier"] == "CRITICAL"


def test_unknown_table_rejected():
    r = requests.post(f"{WS}/write", json={"table": "does_not_exist", "rows": [{"x": 1}]}, timeout=T)
    assert r.status_code == 400
    assert r.json().get("ok") is False
