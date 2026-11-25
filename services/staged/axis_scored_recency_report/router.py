# deps: fastapi, pydantic, sqlalchemy
"""axis_scored_recency_report.py -- recency distribution of the LLM axis scores.

Two layers, mirroring the sibling report modules:
  * pure layer -- build_report(rows) / render_report(rows) take the axis-score
    rows from the caller and touch nothing else (no DB writes, no network, no
    duckdb, stdlib only for the math).
  * data seam -- load_scored_rows(db, model_version) reads the REAL app table
    through the REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, returning rows in the shape the pure layer
    expects. A read-only GET endpoint exposes the same report.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/report", tags=["report"])

STALE_THRESHOLD_DAYS = 30
UNPARSEABLE = "unparseable"


# ---------------------------------------------------------------- pure layer

def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a scored_at into a UTC datetime, or None when unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_days(dt: datetime, now: datetime) -> float:
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recency distribution of the axis scores.

    rows: dicts with keys server_id (str), axis_name (str), scored_at
    (datetime or ISO str), decision_rule_version (str), model_version (str).

    Returns {"total_rows", "servers", "per_axis": {axis: {rows,
    median_age_days, max_age_days, stale_over_30d, distinct_model_versions}},
    "stale_servers", "stale_over_threshold_pct", "unparseable_rows"}.

    Rows whose scored_at cannot be parsed are counted under the pseudo-axis
    "unparseable" and excluded from the age math instead of raising.
    """
    now = datetime.now(timezone.utc)
    per_axis: Dict[str, Dict[str, Any]] = {}
    servers: set = set()
    stale_servers: set = set()
    total_rows = 0
    unparseable_rows = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        total_rows += 1
        sid = str(row.get("server_id") or "").strip()
        axis = str(row.get("axis_name") or "").strip() or UNPARSEABLE
        if sid:
            servers.add(sid)
        entry = per_axis.setdefault(axis, {
            "rows": 0,
            "median_age_days": None,
            "max_age_days": None,
            "stale_over_30d": 0,
            "distinct_model_versions": 0,
        })
        entry["rows"] += 1
        mv = str(row.get("model_version") or "").strip()
        if mv:
            entry.setdefault("_model_versions", set()).add(mv)
        dt = _parse_ts(row.get("scored_at"))
        if dt is None:
            unparseable_rows += 1
            per_axis.setdefault(UNPARSEABLE, {
                "rows": 0,
                "median_age_days": None,
                "max_age_days": None,
                "stale_over_30d": 0,
                "distinct_model_versions": 0,
            })
            per_axis[UNPARSEABLE]["rows"] += 1
            continue
        age = _age_days(dt, now)
        entry.setdefault("_ages", []).append(age)
        if age > STALE_THRESHOLD_DAYS:
            entry["stale_over_30d"] += 1
            if sid:
                stale_servers.add(sid)

    for axis, entry in per_axis.items():
        ages = entry.pop("_ages", [])
        versions = entry.pop("_model_versions", set())
        if ages:
            entry["median_age_days"] = round(float(statistics.median(ages)), 2)
            entry["max_age_days"] = round(float(max(ages)), 2)
        else:
            entry["median_age_days"] = None
            entry["max_age_days"] = None
        entry["stale_over_30d"] = int(entry.get("stale_over_30d") or 0)
        entry["distinct_model_versions"] = len(versions)

    server_count = len(servers)
    pct = round(100.0 * len(stale_servers) / server_count, 2) if server_count else 0.0
    return {
        "total_rows": total_rows,
        "servers": server_count,
        "per_axis": {axis: per_axis[axis] for axis in sorted(per_axis)},
        "stale_servers": len(stale_servers),
        "stale_over_threshold_pct": pct,
        "unparseable_rows": unparseable_rows,
    }


def render_report(rows: List[Dict[str, Any]]) -> str:
    """Render the recency report as markdown (per-axis table + server table)."""
    report = build_report(rows)
    lines: List[str] = []
    lines.append("# Axis score recency")
    lines.append("")
    lines.append(f"- total_rows: {report['total_rows']}")
    lines.append(f"- servers: {report['servers']}")
    lines.append(f"- stale_servers (over {STALE_THRESHOLD_DAYS}d): {report['stale_servers']}")
    lines.append(f"- stale_over_threshold_pct: {report['stale_over_threshold_pct']}")
    lines.append(f"- unparseable_rows: {report['unparseable_rows']}")
    lines.append("")
    lines.append("## Per-axis age")
    lines.append("")
    lines.append("| axis | rows | median_age_days | max_age_days | stale_over_30d | distinct_model_versions |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for axis, entry in (report.get("per_axis") or {}).items():
        lines.append("| {a} | {r} | {m} | {x} | {s} | {v} |".format(
            a=axis,
            r=entry.get("rows", 0),
            m="" if entry.get("median_age_days") is None else entry["median_age_days"],
            x="" if entry.get("max_age_days") is None else entry["max_age_days"],
            s=entry.get("stale_over_30d", 0),
            v=entry.get("distinct_model_versions", 0),
        ))
    lines.append("")
    lines.append("## Servers")
    lines.append("")
    lines.append("| server_id | rows | max_age_days | stale_over_30d |")
    lines.append("| --- | --- | --- | --- |")
    now = datetime.now(timezone.utc)
    per_server: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("server_id") or "").strip()
        if not sid:
            continue
        slot = per_server.setdefault(sid, {"rows": 0, "max_age_days": None, "stale": 0})
        slot["rows"] += 1
        dt = _parse_ts(row.get("scored_at"))
        if dt is None:
            continue
        age = _age_days(dt, now)
        if slot["max_age_days"] is None or age > slot["max_age_days"]:
            slot["max_age_days"] = round(age, 2)
        if age > STALE_THRESHOLD_DAYS:
            slot["stale"] += 1
    for sid in sorted(per_server):
        slot = per_server[sid]
        lines.append("| {s} | {r} | {m} | {st} |".format(
            s=sid,
            r=slot["rows"],
            m="" if slot["max_age_days"] is None else slot["max_age_days"],
            st=slot["stale"],
        ))
    if not per_server:
        lines.append("| (none) | 0 |  | 0 |")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

def _latest_model_version(db: Session) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def load_scored_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as report rows."""
    mv = model_version or _latest_model_version(db)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    )
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    return [
        {"server_id": sid, "axis_name": axis, "scored_at": scored_at,
         "decision_rule_version": drv, "model_version": mver}
        for sid, axis, scored_at, drv, mver in db.execute(stmt).all()
    ]


# ------------------------------------------------------------------- API

class AxisRecencyStats(BaseModel):
    rows: int
    median_age_days: Optional[float] = None
    max_age_days: Optional[float] = None
    stale_over_30d: int
    distinct_model_versions: int


class AxisRecencyReport(BaseModel):
    total_rows: int
    servers: int
    per_axis: Dict[str, AxisRecencyStats]
    stale_servers: int
    stale_over_threshold_pct: float
    unparseable_rows: int


@router.get("/axis-scored-recency", response_model=AxisRecencyReport)
def axis_scored_recency(db: Session = Depends(get_session),
                        model_version: Optional[str] = None) -> dict:
    """Recency distribution of the scored axis rows (read-only)."""
    return build_report(load_scored_rows(db, model_version))


# ------------------------------------------------------------------- CLI

def run() -> int:
    """Read the axis rows and write a JSON summary to stdout (no DB writes)."""
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        rows = load_scored_rows(session)
    finally:
        session.close()
    report = build_report(rows)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- pure layer: 3 rows, 2 servers (5d, 60d, malformed scored_at) ---
    now = datetime.now(timezone.utc)
    rows = [
        {"server_id": "srv_fresh", "axis_name": "overall_risk",
         "scored_at": (now - timedelta(days=5)).isoformat(),
         "decision_rule_version": "v3", "model_version": "v3.0_40974559"},
        {"server_id": "srv_stale", "axis_name": "overall_risk",
         "scored_at": (now - timedelta(days=60)).isoformat(),
         "decision_rule_version": "v3", "model_version": "v3.0_40974559"},
        {"server_id": "srv_stale", "axis_name": "auth_strength",
         "scored_at": "not-a-timestamp",
         "decision_rule_version": "v3", "model_version": "v3.0_40974559"},
    ]
    report = build_report(rows)
    assert report["total_rows"] == 3, report
    assert report["servers"] == 2, report
    overall = report["per_axis"]["overall_risk"]
    assert overall["rows"] == 2, overall
    assert overall["stale_over_30d"] == 1, overall
    assert overall["max_age_days"] >= 59.0, overall
    assert overall["median_age_days"] is not None, overall
    assert overall["distinct_model_versions"] == 1, overall
    assert report["unparseable_rows"] == 1, report
    assert report["per_axis"][UNPARSEABLE]["rows"] == 1, report
    assert report["stale_servers"] == 1, report
    assert report["stale_over_threshold_pct"] == 50.0, report

    md = render_report(rows)
    assert "axis" in md.lower(), md
    assert "srv_stale" in md and "srv_fresh" in md, md
    assert "overall_risk" in md, md

    # --- edge case: empty input must not raise ---
    empty = build_report([])
    assert empty["total_rows"] == 0 and empty["servers"] == 0, empty
    assert empty["per_axis"] == {}, empty
    assert empty["stale_over_threshold_pct"] == 0.0, empty
    assert render_report([])

    # --- DB path: real app models over SQLite, endpoint via dependency override ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        for i, (sid, ax, lbl) in enumerate((
                ("srv1", "overall_risk", "HIGH"),
                ("srv1", "auth_strength", "STRONG"),
                ("srv2", "overall_risk", "LOW")), start=1):
            s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label=lbl,
                                  model_version="v3.0_40974559"))
        s.commit()
        loaded = load_scored_rows(s)
        assert len(loaded) == 3, loaded
        db_report = build_report(loaded)
        assert db_report["total_rows"] == 3, db_report
        assert db_report["servers"] == 2, db_report
        assert db_report["per_axis"]["overall_risk"]["rows"] == 2, db_report
    finally:
        s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    resp = client.get("/api/report/axis-scored-recency")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_rows"] == 3, body
    assert body["servers"] == 2, body
    assert "per_axis" in body and body["per_axis"]["overall_risk"]["rows"] == 2, body

    print("PASS")
