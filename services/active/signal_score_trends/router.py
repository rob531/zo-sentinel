# deps: fastapi, pydantic, sqlalchemy, requests
"""Signal Score Trends Service.

Provides trend and distribution endpoints for signal scores:
  - GET /api/signal_score_trends/overview       -- aggregate signal score stats
  - GET /api/signal_score_trends/trend         -- time-series per signal_name
  - GET /api/signal_score_trends/server/{id}  -- per-server signal history

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint — no auth required.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/signal_score_trends", tags=["signal_score_trends"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class SignalStat(BaseModel):
    signal_name: str
    count: int
    avg_score: float | None
    min_score: float | None
    max_score: float | None


class OverviewResponse(BaseModel):
    as_of: str
    total_servers: int
    scored_servers: int
    signal_stats: List[SignalStat]


class TrendPoint(BaseModel):
    day: str
    signal_name: str
    avg_score: float | None
    count: int


class TrendResponse(BaseModel):
    signal_name: str | None
    days: int
    points: List[TrendPoint]


class ServerSignalSnapshot(BaseModel):
    signal_name: str
    score: float
    scored_at: str | None


class ServerSignalHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: str | None
    trend_days: int
    snapshots: List[ServerSignalSnapshot]


# --------------------------------------------------------------------------- #
# Mesh helpers
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> list[dict]:
    """Read-only query against the ZoComputer mesh store."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql, "params": params or {}},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Mesh query failed: {exc}")
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=502, detail=data["error"])
    if not isinstance(data, list):
        return []
    return data


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/overview", response_model=OverviewResponse)
def get_signal_overview(
    db: Session = Depends(get_session),
) -> OverviewResponse:
    """
    Aggregate stats per signal_name from mcp_signal_scores (mesh) plus
    server counts from the app registry.
    """
    # Count scored vs total servers from app
    total_servers: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    scored_server_ids: List[str] = db.execute(
        select(McpLlmAxisScore.server_id).distinct()
    ).scalars().all()
    scored_count = len(scored_server_ids)

    # Signal stats from mesh
    rows = _query_mesh(
        """
        SELECT
            signal_name,
            COUNT(*)        AS cnt,
            AVG(score)      AS avg_score,
            MIN(score)      AS min_score,
            MAX(score)      AS max_score
        FROM mcp_signal_scores
        GROUP BY signal_name
        ORDER BY signal_name
        """
    )

    signal_stats = [
        SignalStat(
            signal_name=r.get("signal_name", ""),
            count=r.get("cnt") or 0,
            avg_score=round(r.get("avg_score"), 4) if r.get("avg_score") is not None else None,
            min_score=round(r.get("min_score"), 4) if r.get("min_score") is not None else None,
            max_score=round(r.get("max_score"), 4) if r.get("max_score") is not None else None,
        )
        for r in rows
    ]

    return OverviewResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_servers=total_servers,
        scored_servers=scored_count,
        signal_stats=signal_stats,
    )


@router.get("/trend", response_model=TrendResponse)
def get_signal_trend(
    signal_name: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TrendResponse:
    """
    Time-series of average signal scores grouped by day.
    Optionally filter to a single signal_name.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    if signal_name:
        rows = _query_mesh(
            """
            SELECT
                DATE(scored_at) AS day,
                signal_name,
                AVG(score) AS avg_score,
                COUNT(*)   AS cnt
            FROM mcp_signal_scores
            WHERE signal_name = :signal_name
              AND DATE(scored_at) >= :cutoff
            GROUP BY DATE(scored_at), signal_name
            ORDER BY day ASC
            """,
            {"signal_name": signal_name, "cutoff": cutoff},
        )
    else:
        rows = _query_mesh(
            """
            SELECT
                DATE(scored_at) AS day,
                signal_name,
                AVG(score) AS avg_score,
                COUNT(*)   AS cnt
            FROM mcp_signal_scores
            WHERE DATE(scored_at) >= :cutoff
            GROUP BY DATE(scored_at), signal_name
            ORDER BY day ASC, signal_name
            """,
            {"cutoff": cutoff},
        )

    points = [
        TrendPoint(
            day=r.get("day", ""),
            signal_name=r.get("signal_name", ""),
            avg_score=round(r.get("avg_score"), 4) if r.get("avg_score") is not None else None,
            count=r.get("cnt") or 0,
        )
        for r in rows
    ]

    return TrendResponse(signal_name=signal_name, days=days, points=points)


@router.get("/server/{server_id}", response_model=ServerSignalHistoryResponse)
def get_server_signal_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerSignalHistoryResponse:
    """
    Per-server signal score history: latest score per signal_name in the window.
    """
    # Verify server exists in app registry
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = _query_mesh(
        """
        SELECT
            signal_name,
            score,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = :server_id
          AND DATE(scored_at) >= :cutoff
        ORDER BY scored_at DESC
        """,
        {"server_id": server_id, "cutoff": cutoff},
    )

    # Deduplicate: keep latest row per signal_name
    seen: set = set()
    snapshots: List[ServerSignalSnapshot] = []
    for r in rows:
        sn = r.get("signal_name", "")
        if sn and sn not in seen:
            seen.add(sn)
            scored_at_raw = r.get("scored_at")
            if isinstance(scored_at_raw, datetime):
                scored_at_str = scored_at_raw.strftime("%Y-%m-%dT%H:%M:%SZ")
            elif scored_at_raw:
                scored_at_str = str(scored_at_raw)
            else:
                scored_at_str = None
            snapshots.append(
                ServerSignalSnapshot(
                    signal_name=sn,
                    score=round(r.get("score", 0.0), 4),
                    scored_at=scored_at_str,
                )
            )

    return ServerSignalHistoryResponse(
        server_id=server_id,
        name=srv,
        trend_days=days,
        snapshots=snapshots,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite: test override for app.db
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestSession()
    db.add(McpServerRegistry(server_id="srv-test-1", name="Test Server"))
    db.commit()
    db.close()

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Overview — empty mesh returns []
    r = client.get("/api/signal_score_trends/overview")
    assert r.status_code == 200, f"overview failed: {r.text}"
    d = r.json()
    assert "total_servers" in d
    assert "signal_stats" in d

    # Trend — empty mesh
    r2 = client.get("/api/signal_score_trends/trend?days=7")
    assert r2.status_code == 200, f"trend failed: {r2.text}"
    d2 = r2.json()
    assert "points" in d2

    # Server history — 404 if not found in mesh (server not in mesh)
    r3 = client.get("/api/signal_score_trends/server/srv-test-1?days=7")
    assert r3.status_code == 200, f"server history failed: {r3.text}"
    d3 = r3.json()
    assert d3["server_id"] == "srv-test-1"
    assert d3["snapshots"] == []

    print("Self-test PASSED")
