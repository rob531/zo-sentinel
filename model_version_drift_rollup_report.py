"""model_version_drift_rollup_report.py -- decision-rule / model drift rollup.

Summarises which (decision_rule_version, model_version, adapter_sha256)
combinations are currently writing rows into the scoring table and how stale
each of them is.

Contract:
    build_report(days: int = 30, rows: list[dict] | None = None) -> dict
    input row: {server_id, axis_name, decision_rule_version, model_version,
                adapter_sha256, scored_at}
    output: {generated_at, days,
             versions: [{decision_rule_version, model_version, adapter_sha256,
                         servers, newest_scored_at, age_days, stale}],
             stale_versions, total_versions}

Pure read: no writes to any table, no duckdb import, no import-time side
effects. Data comes from the REAL app data layer (app.db / app.models) with a
write_service fallback query.
"""
# deps: requests
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

WRITE_SERVICE = "http://127.0.0.1:8772"

_COLUMNS = ("server_id", "axis_name", "decision_rule_version", "model_version",
            "adapter_sha256", "scored_at")


def _parse_dt(value: Any) -> Optional[datetime]:
    """scored_at may be a datetime (DB) or an ISO-8601 string (pre-fetched rows)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def _load_rows_from_db(days: int, db: Optional[Session] = None) -> list[dict]:
    """Read the scoring rows for the window from mcp_llm_axis_scores (app Postgres)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    if db is None:
        from app.db import SessionLocal
        session = SessionLocal()
        try:
            return _load_rows_from_db(days, session)
        finally:
            session.close()
    stmt = select(McpLlmAxisScore.server_id,
                  McpLlmAxisScore.axis_name,
                  McpLlmAxisScore.decision_rule_version,
                  McpLlmAxisScore.model_version,
                  McpLlmAxisScore.adapter_sha256,
                  McpLlmAxisScore.scored_at)
    out: list[dict] = []
    for row in db.execute(stmt).all():
        server_id, axis_name, drv, model_version, adapter_sha256, scored_at = row
        scored = _parse_dt(scored_at)
        if scored is None or scored < cutoff:
            continue
        out.append({"server_id": server_id, "axis_name": axis_name,
                    "decision_rule_version": drv, "model_version": model_version,
                    "adapter_sha256": adapter_sha256, "scored_at": scored})
    return out


def _load_rows_from_write_service(days: int) -> list[dict]:
    """Fallback: parameterized read through write_service /query."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    sql = ("SELECT server_id, axis_name, decision_rule_version, model_version, "
           "adapter_sha256, scored_at FROM mcp_llm_axis_scores "
           "WHERE scored_at >= :cutoff")
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query",
                             json={"sql": sql,
                                   "params": {"cutoff": cutoff.isoformat()}},
                             timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    rows = data.get("rows", []) if isinstance(data, dict) else data
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            item = {k: row.get(k) for k in _COLUMNS}
        elif isinstance(row, (list, tuple)):
            item = dict(zip(_COLUMNS, row))
        else:
            continue
        out.append(item)
    return out


def load_rows(days: int = 30, db: Optional[Session] = None) -> list[dict]:
    """Window rows from the app DB; write_service is the fallback path."""
    try:
        return _load_rows_from_db(days, db)
    except Exception:
        return _load_rows_from_write_service(days)


def load_rows_dep(days: int = 30, db: Session = Depends(get_session)) -> list[dict]:
    """Dependency-injected variant (app.db session seam)."""
    return load_rows(days=days, db=db)


def build_report(days: int = 30, rows: Optional[list] = None) -> dict:
    """Roll up decision-rule / model / adapter drift.

    rows: pre-fetched scoring rows; when None they are read from the scoring
    table (app DB session first, write_service fallback).
    """
    days = int(days)
    if days < 0:
        days = 0
    if rows is None:
        rows = load_rows(days=days)

    now = datetime.now(timezone.utc)
    groups: dict[tuple, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scored_at = _parse_dt(row.get("scored_at"))
        if scored_at is None:
            continue
        key = (_text(row.get("decision_rule_version")),
               _text(row.get("model_version")),
               _text(row.get("adapter_sha256")))
        bucket = groups.get(key)
        if bucket is None:
            bucket = {"servers": set(), "newest": scored_at}
            groups[key] = bucket
        bucket["servers"].add(str(row.get("server_id")))
        if scored_at > bucket["newest"]:
            bucket["newest"] = scored_at

    versions = []
    for (drv, model_version, adapter_sha256), bucket in groups.items():
        age_days = round((now - bucket["newest"]).total_seconds() / 86400.0, 2)
        versions.append({
            "decision_rule_version": drv,
            "model_version": model_version,
            "adapter_sha256": adapter_sha256,
            "servers": len(bucket["servers"]),
            "newest_scored_at": _iso(bucket["newest"]),
            "age_days": age_days,
            "stale": age_days > days,
        })
    versions.sort(key=lambda v: (-v["servers"], v["model_version"]))

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "versions": versions,
        "stale_versions": sum(1 for v in versions if v["stale"]),
        "total_versions": len(versions),
    }


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via the app models
    from datetime import timedelta as _td
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- synthetic rows path (no DB) ---
    now = datetime.now(timezone.utc)
    fresh = now - _td(days=1)
    old = now - _td(days=60)
    synthetic = [
        {"server_id": "srv_fresh", "axis_name": "overall_risk",
         "decision_rule_version": "dr-v1", "model_version": "v3.0_40974559",
         "adapter_sha256": "sha-a", "scored_at": fresh},
        {"server_id": "srv_fresh", "axis_name": "auth_strength",
         "decision_rule_version": "dr-v1", "model_version": "v3.0_40974559",
         "adapter_sha256": "sha-a", "scored_at": fresh},
        {"server_id": "srv_old", "axis_name": "overall_risk",
         "decision_rule_version": "dr-v0", "model_version": "v2.9_40970000",
         "adapter_sha256": "sha-b", "scored_at": old},
    ]
    rep = build_report(days=30, rows=synthetic)
    assert rep["total_versions"] == 2, rep
    assert rep["stale_versions"] == 1, rep
    assert rep["days"] == 30, rep
    fresh_v = [v for v in rep["versions"] if v["model_version"] == "v3.0_40974559"][0]
    assert fresh_v["stale"] is False, fresh_v
    assert fresh_v["servers"] == 1, fresh_v
    assert fresh_v["decision_rule_version"] == "dr-v1", fresh_v
    assert fresh_v["adapter_sha256"] == "sha-a", fresh_v
    old_v = [v for v in rep["versions"] if v["model_version"] == "v2.9_40970000"][0]
    assert old_v["stale"] is True, old_v
    assert old_v["age_days"] > 30, old_v
    datetime.fromisoformat(rep["generated_at"])  # ISO 8601
    json.dumps(rep)  # JSON-serialisable

    # --- edge case: no rows ---
    empty = build_report(days=30, rows=[])
    assert empty["total_versions"] == 0 and empty["stale_versions"] == 0, empty
    assert empty["versions"] == [], empty

    # --- DB path: real app models over SQLite ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="db1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="db2", axis_name="overall_risk",
                          label="LOW", model_version="v2.9_40970000"))
    s.commit()

    db_rows = _load_rows_from_db(30, s)
    s.close()
    db_rep = build_report(days=30, rows=db_rows)
    assert db_rep["total_versions"] == 2, db_rep
    assert db_rep["stale_versions"] == 0, db_rep
    assert all(v["servers"] == 1 for v in db_rep["versions"]), db_rep

    print(json.dumps(rep, indent=2, default=str))
    print("PASS")
