# deps: fastapi, pydantic, sqlalchemy, requests
"""Risk Tier Trend Dashboard API.

Returns risk tier distribution over time from the mesh store (mcp_signal_scores)
and current tier snapshot from the app table (mcp_server_registry).

Auth: public.
Data: mesh via write_service, app table via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path before any app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_trend_dashboard_api"])


# --- Pydantic models ---------------------------------------------------------

class TierDataPoint(BaseModel):
    date: str = Field(..., description="ISO date string (YYYY-MM-DD)")
    tier: str = Field(..., description="Risk tier label")
    count: int = Field(..., ge=0, description="Number of servers in this tier on this date")


class TrendSummary(BaseModel):
    tier: str
    count: int
    pct: float


class TrendResponse(BaseModel):
    days: int
    series: list[TierDataPoint]
    summary: list[TrendSummary]
    total: int
    as_of: str = Field(..., description="ISO 8601 timestamp of generation")


# --- Mesh query helper -------------------------------------------------------

def _mesh_query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only query against the ZoComputer mesh store."""
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}},
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Mesh query failed")
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=502, detail=data["error"])
    return data if isinstance(data, list) else []


# --- Endpoints ---------------------------------------------------------------

@router.get("/risk/tier-trend-dashboard", response_model=TrendResponse)
def get_risk_tier_trend_dashboard(
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> TrendResponse:
    """Return risk tier distribution over time and current snapshot.

    Time-series data is read from the mesh store (mcp_signal_scores).
    Current-tier snapshot is read from the app table (mcp_server_registry).
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    as_of = datetime.now(timezone.utc).isoformat()

    # Time-series: count of servers per tier per day from mesh store
    series: list[TierDataPoint] = []
    try:
        mesh_rows = _mesh_query(
            """
            SELECT
                COALESCE(risk_tier, 'UNKNOWN') AS tier,
                DATE(assessed_at) AS day,
                COUNT(DISTINCT server_id) AS count
            FROM mcp_signal_scores
            WHERE assessed_at >= :cutoff
            GROUP BY DATE(assessed_at), COALESCE(risk_tier, 'UNKNOWN')
            ORDER BY day ASC, tier
            """,
            {"cutoff": cutoff},
        )
        for row in mesh_rows:
            day_val = row.get("day") or row.get("DATE(assessed_at)") or ""
            series.append(
                TierDataPoint(
                    date=str(day_val),
                    tier=str(row.get("tier", "UNKNOWN")),
                    count=int(row.get("count", 0)),
                )
            )
    except Exception:
        # Mesh store may be unavailable; return empty series
        series = []

    # Current snapshot: tier distribution from app registry
    summary: list[TrendSummary] = []
    result = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        ).group_by(McpServerRegistry.risk_tier)
    ).all()
    total = sum(r.count for r in result)
    for r in result:
        tier = r.risk_tier or "UNKNOWN"
        summary.append(
            TrendSummary(
                tier=tier,
                count=r.count,
                pct=round(r.count / total * 100, 2) if total else 0.0,
            )
        )

    return TrendResponse(
        days=days,
        series=series,
        summary=summary,
        total=total,
        as_of=as_of,
    )


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite for self-test (override get_session)
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        return TestSessionLocal()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # Seed test data
    with TestSessionLocal() as sess:
        sess.add_all([
            McpServerRegistry(server_id="srv-1", name="Server Alpha", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv-2", name="Server Beta",  risk_tier="MEDIUM"),
            McpServerRegistry(server_id="srv-3", name="Server Gamma", risk_tier="LOW"),
            McpServerRegistry(server_id="srv-4", name="Server Delta", risk_tier="CRITICAL"),
            McpServerRegistry(server_id="srv-5", name="Server Epsilon", risk_tier="HIGH"),
        ])
        sess.commit()

    # Mock mesh store: return synthetic time-series
    fake_mesh_rows = [
        {"tier": "HIGH",     "day": "2026-08-25", "count": 3},
        {"tier": "MEDIUM",   "day": "2026-08-25", "count": 1},
        {"tier": "LOW",      "day": "2026-08-25", "count": 1},
        {"tier": "HIGH",     "day": "2026-08-26", "count": 2},
        {"tier": "CRITICAL", "day": "2026-08-26", "count": 1},
        {"tier": "MEDIUM",   "day": "2026-08-26", "count": 1},
        {"tier": "LOW",      "day": "2026-08-26", "count": 1},
    ]

    class FakeResponse:
        status_code = 200
        def json(self):
            return fake_mesh_rows

    # Test: happy path (mesh mocked)
    with patch("requests.post", return_value=FakeResponse()):
        resp = client.get("/api/risk/tier-trend-dashboard?days=7")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        _sys.exit(1)
    data = resp.json()
    if "days" not in data:
        print("FAIL: missing 'days' in response")
        _sys.exit(1)
    if data["days"] != 7:
        print(f"FAIL: expected days=7, got {data['days']}")
        _sys.exit(1)
    if "summary" not in data:
        print("FAIL: missing 'summary' in response")
        _sys.exit(1)
    if "total" not in data:
        print("FAIL: missing 'total' in response")
        _sys.exit(1)
    if data["total"] != 5:
        print(f"FAIL: expected total=5, got {data['total']}")
        _sys.exit(1)
    # Verify summary has the seeded tiers
    tier_counts = {s["tier"]: s["count"] for s in data["summary"]}
    if tier_counts.get("HIGH") != 2:
        print(f"FAIL: expected HIGH count=2, got {tier_counts}")
        _sys.exit(1)
    if tier_counts.get("CRITICAL") != 1:
        print(f"FAIL: expected CRITICAL count=1, got {tier_counts}")
        _sys.exit(1)
    if tier_counts.get("MEDIUM") != 1:
        print(f"FAIL: expected MEDIUM count=1, got {tier_counts}")
        _sys.exit(1)
    if tier_counts.get("LOW") != 1:
        print(f"FAIL: expected LOW count=1, got {tier_counts}")
        _sys.exit(1)
    if not isinstance(data.get("series"), list):
        print("FAIL: 'series' must be a list")
        _sys.exit(1)
    if len(data["series"]) != 7:
        print(f"FAIL: expected 7 series rows from mock mesh, got {len(data['series'])}")
        _sys.exit(1)

    # Test: mesh unavailable gracefully degrades to empty series
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("no mesh")):
        resp2 = client.get("/api/risk/tier-trend-dashboard?days=30")
    if resp2.status_code != 200:
        print(f"FAIL: should gracefully degrade when mesh unavailable, got {resp2.status_code}")
        _sys.exit(1)
    data2 = resp2.json()
    if data2.get("total") != 5:
        print(f"FAIL: app-tier data should still be returned even if mesh fails, got total={data2.get('total')}")
        _sys.exit(1)
    if data2.get("series") != []:
        print(f"FAIL: series should be empty list when mesh fails, got {data2.get('series')}")
        _sys.exit(1)

    # Test: auth is not required (public endpoint)
    resp3 = client.get("/api/risk/tier-trend-dashboard?days=1")
    if resp3.status_code != 200:
        print(f"FAIL: public endpoint should not require auth, got {resp3.status_code}")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
