"""axis_score_staleness_report.py -- how stale are the per-axis LLM scores?

Reads the scored-axis rows from the app Postgres (mcp_llm_axis_scores) through
the REAL app data layer (app.db / app.models -- no inline stubs, no mocks) and
reports, per axis, how many servers lag behind the freshness threshold so the
3-tier app surface can show which axes are lagging.

Contract:
    build_report(days: int = 30, rows: list[dict] | None = None) -> dict
    input row: {server_id, axis_name, scored_at}
    output: {generated_at, threshold_days, servers_total, servers_stale,
              axes: [{axis_name, servers, stale_servers, stale_pct,
                      oldest_scored_at, newest_scored_at}],
              stale_server_ids: [...]}

A server is STALE when its NEWEST scored_at is older than now - days.
Pure read: no writes to any table, no network, no protected-module imports.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["staleness"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


def _parse_dt(value: Any) -> Optional[datetime]:
    """scored_at may be a datetime (DB) or an ISO-8601 string (pre-fetched rows)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _load_rows(db: Session) -> list[dict]:
    """Read (server_id, axis_name, scored_at) from mcp_llm_axis_scores."""
    out = []
    for server_id, axis_name, scored_at in db.execute(
            select(McpLlmAxisScore.server_id,
                   McpLlmAxisScore.axis_name,
                   McpLlmAxisScore.scored_at)).all():
        out.append({"server_id": server_id, "axis_name": axis_name,
                    "scored_at": scored_at})
    return out


def build_report(days: int = 30, rows: Optional[list] = None,
                 db: Optional[Session] = None) -> dict:
    """Measure per-axis score staleness.

    rows: pre-fetched [{server_id, axis_name, scored_at}]; when None the rows
    are read from mcp_llm_axis_scores via the app DB session.
    """
    days = int(days)
    if days < 0:
        days = 0
    if rows is None:
        if db is None:
            from app.db import SessionLocal
            session = SessionLocal()
            try:
                rows = _load_rows(session)
            finally:
                session.close()
        else:
            rows = _load_rows(db)

    now = datetime.utcnow()
    threshold = now - timedelta(days=days)

    server_newest: dict[str, datetime] = {}
    per_axis: dict[str, list[dict]] = {axis: [] for axis in AXES}
    server_ids: list[str] = []

    for row in rows or []:
        server_id = row.get("server_id")
        axis_name = row.get("axis_name")
        scored_at = _parse_dt(row.get("scored_at"))
        if not server_id or not axis_name or scored_at is None:
            continue
        if server_id not in server_newest:
            server_ids.append(server_id)
            server_newest[server_id] = scored_at
        elif scored_at > server_newest[server_id]:
            server_newest[server_id] = scored_at
        if axis_name in per_axis:
            per_axis[axis_name].append({"server_id": server_id, "scored_at": scored_at})

    stale_ids = sorted(sid for sid, newest in server_newest.items() if newest < threshold)
    stale_set = set(stale_ids)
    servers_total = len(server_newest)

    axes = []
    for axis in AXES:
        axis_rows = per_axis[axis]
        axis_servers = {r["server_id"] for r in axis_rows}
        stale_servers = sorted(axis_servers & stale_set)
        servers = len(axis_servers)
        stale_pct = (len(stale_servers) * 100.0 / servers) if servers else 0.0
        stamps = [r["scored_at"] for r in axis_rows]
        axes.append({
            "axis_name": axis,
            "servers": servers,
            "stale_servers": len(stale_servers),
            "stale_pct": round(stale_pct, 2),
            "oldest_scored_at": _iso(min(stamps)) if stamps else None,
            "newest_scored_at": _iso(max(stamps)) if stamps else None,
        })

    return {
        "generated_at": now.isoformat(),
        "threshold_days": days,
        "servers_total": servers_total,
        "servers_stale": len(stale_ids),
        "axes": axes,
        "stale_server_ids": stale_ids,
    }


@router.get("/reports/axis-score-staleness")
def axis_score_staleness_report(days: int = 30,
                                db: Session = Depends(get_session)) -> dict:
    """Per-axis score staleness read straight from the app Postgres."""
    return build_report(days=days, db=db)


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- synthetic rows path (no DB) ---
    now = datetime.utcnow()
    fresh = now - timedelta(days=1)
    old = now - timedelta(days=60)
    synthetic = [
        {"server_id": "srv_fresh", "axis_name": "overall_risk", "scored_at": fresh},
        {"server_id": "srv_fresh", "axis_name": "auth_strength", "scored_at": fresh},
        {"server_id": "srv_old", "axis_name": "overall_risk", "scored_at": old},
    ]
    rep = build_report(days=30, rows=synthetic)
    assert rep["servers_total"] == 2, rep
    assert "srv_old" in rep["stale_server_ids"], rep
    assert rep["servers_stale"] == 1, rep
    overall = [a for a in rep["axes"] if a["axis_name"] == "overall_risk"][0]
    assert overall["stale_pct"] == 50.0, overall
    assert len(rep["axes"]) == 7, rep
    assert overall["oldest_scored_at"] and overall["newest_scored_at"], overall

    # --- edge case: no rows (division by zero guarded) ---
    empty = build_report(days=30, rows=[])
    assert empty["servers_total"] == 0 and empty["servers_stale"] == 0, empty
    assert all(a["stale_pct"] == 0.0 for a in empty["axes"]), empty

    # --- DB path: real models over SQLite via dependency override ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="db1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="db2", axis_name="overall_risk",
                          label="LOW", model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/reports/axis-score-staleness?days=30")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["servers_total"] == 2, j
    assert j["threshold_days"] == 30, j
    assert len(j["axes"]) == 7, j

    print(json.dumps(rep, indent=2))
    print("PASS")
