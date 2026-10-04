# deps: fastapi, pydantic, sqlalchemy, requests
"""risk_tier_distribution_trend -- risk tier distribution over time.

GET /api/risk-tier-distribution-trend/distribution
  Returns daily risk-tier distribution snapshots across the MCP registry
  over a configurable lookback window, optionally filtered by risk tier.

GET /api/risk-tier-distribution-trend/servers/{server_id}
  Returns per-server tier distribution trend.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app Postgres via get_session + SQLAlchemy models; mesh/pipeline
  tables via write_service HTTP on 127.0.0.1:8772.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_distribution_trend"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    tier: str
    count: int
    pct: float


class DistributionPoint(BaseModel):
    date: str
    total_servers: int
    distribution: List[TierCount]


class DistributionTrendResponse(BaseModel):
    as_of: str
    lookback_days: int
    points: List[DistributionPoint]


class ServerTierTrendEntry(BaseModel):
    date: str
    risk_tier: str | None


class ServerTierTrendResponse(BaseModel):
    server_id: str
    server_name: str | None
    trend: List[ServerTierTrendEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> List[dict]:
    """Execute a read-only query against the mesh store via write_service."""
    payload = {"sql": sql, "params": list(params.values()) if params else []}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mesh query failed: {exc}")
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=502, detail=data["error"])
    if isinstance(data, dict):
        return data.get("rows", [])
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk-tier-distribution-trend/distribution",
    response_model=DistributionTrendResponse,
)
def get_distribution_trend(
    days: int = Query(default=30, ge=1, le=365),
    tier: Optional[str] = Query(default=None, description="Filter to a specific risk tier"),
    db: Session = Depends(get_session),
) -> DistributionTrendResponse:
    """
    Return daily risk-tier distribution snapshots across all servers.

    Data sources:
    - Current snapshot: mcp_server_registry (app Postgres)
    - Historical trend: mcp_signal_scores (mesh store via write_service)
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()

    # Current snapshot from app registry
    total_now: int = db.scalar(
        select(func.count(McpServerRegistry.server_id))
    ) or 0

    tier_counts: dict[str, int] = {}
    rows = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id),
        ).group_by(McpServerRegistry.risk_tier)
    ).all()
    for tier_name, cnt in rows:
        key = tier_name or "UNKNOWN"
        tier_counts[key] = cnt

    # Historical trend from mesh store
    if tier:
        sql = """
            SELECT
                DATE(assessed_at) AS day,
                COUNT(DISTINCT server_id) AS total_servers,
                COUNT(*) AS row_count
            FROM mcp_signal_scores
            WHERE risk_tier = :tier
              AND assessed_at >= :cutoff
            GROUP BY DATE(assessed_at)
            ORDER BY day ASC
        """
        params = {"tier": tier, "cutoff": cutoff}
    else:
        sql = """
            SELECT
                DATE(assessed_at) AS day,
                risk_tier,
                COUNT(DISTINCT server_id) AS count
            FROM mcp_signal_scores
            WHERE assessed_at >= :cutoff
            GROUP BY DATE(assessed_at), risk_tier
            ORDER BY day ASC, risk_tier
        """
        params = {"cutoff": cutoff}

    try:
        history_rows = _query_mesh(sql, params)
    except HTTPException:
        # Fall back to empty trend if mesh is unavailable
        history_rows = []

    # Build a map: date -> {tier -> count}
    date_map: dict[str, dict[str, int]] = {}
    if tier:
        # When filtered by tier, each day has one entry with row_count
        for row in history_rows:
            day = str(row.get("day", ""))
            if not day:
                continue
            cnt = int(row.get("row_count") or 0)
            ts = int(row.get("total_servers") or 0)
            date_map.setdefault(day, {})["TOTAL"] = ts
            date_map[day][tier] = date_map[day].get(tier, 0) + cnt
    else:
        for row in history_rows:
            day = str(row.get("day", ""))
            t = str(row.get("risk_tier") or "UNKNOWN")
            cnt = int(row.get("count") or 0)
            if not day:
                continue
            date_map.setdefault(day, {})[t] = date_map[day].get(t, 0) + cnt

    # If no history, use current snapshot as the single point
    if not date_map:
        today_str = now.date().isoformat()
        dist = [
            TierCount(tier=t, count=c, pct=round(c / total_now * 100, 1) if total_now else 0)
            for t, c in sorted(tier_counts.items())
        ]
        date_map[today_str] = {t: c for t, c in tier_counts.items()}
    else:
        # Fill in today's snapshot for any missing days at the end
        today_str = now.date().isoformat()
        if today_str not in date_map:
            date_map[today_str] = dict(tier_counts)

    # Build ordered points
    points: List[DistributionPoint] = []
    for day_str in sorted(date_map.keys()):
        day_counts = date_map[day_str]
        total_day = sum(day_counts.values()) or 1
        dist_list = [
            TierCount(tier=t, count=c, pct=round(c / total_day * 100, 1))
            for t, c in sorted(day_counts.items())
        ]
        points.append(DistributionPoint(
            date=day_str,
            total_servers=total_day,
            distribution=dist_list,
        ))

    return DistributionTrendResponse(
        as_of=now.isoformat(),
        lookback_days=days,
        points=points,
    )


@router.get(
    "/risk-tier-distribution-trend/servers/{server_id}",
    response_model=ServerTierTrendResponse,
)
def get_server_tier_trend(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerTierTrendResponse:
    """
    Return the per-server risk-tier trend for a specific server.
    Returns empty trend if no history found.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()

    # Look up current name from app registry
    server = db.execute(
        select(McpServerRegistry.name).where(
            McpServerRegistry.server_id == server_id
        )
    ).scalar_one_or_none()

    # Historical tier from mesh store
    sql = """
        SELECT DISTINCT DATE(assessed_at) AS day, risk_tier
        FROM mcp_signal_scores
        WHERE server_id = :server_id
          AND assessed_at >= :cutoff
        ORDER BY day ASC
    """
    try:
        rows = _query_mesh(sql, {"server_id": server_id, "cutoff": cutoff})
    except HTTPException:
        rows = []

    trend = [
        ServerTierTrendEntry(
            date=str(r.get("day", "")),
            risk_tier=r.get("risk_tier"),
        )
        for r in rows
        if r.get("day")
    ]

    return ServerTierTrendResponse(
        server_id=server_id,
        server_name=server,
        trend=trend,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock, patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(512),
                registry_source VARCHAR(64),
                url TEXT,
                description TEXT,
                trust_score FLOAT,
                verdict VARCHAR(64),
                verdict_reasoning TEXT,
                confidence FLOAT,
                last_assessed TIMESTAMP,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                risk_tier VARCHAR(32),
                meta TEXT
            )
        """))
        conn.commit()

    with engine.connect() as conn:
        for sid, name, rt in [
            ("srv1", "Alpha",   "HIGH"),
            ("srv2", "Beta",    "MEDIUM"),
            ("srv3", "Gamma",   "LOW"),
            ("srv4", "Delta",   "HIGH"),
        ]:
            conn.execute(text(
                "INSERT INTO mcp_server_registry VALUES (:sid, :name, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, :rt, NULL)"
            ), {"sid": sid, "name": name, "rt": rt})
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    # Mock requests.post to return simulated mesh history
    class _FakeResponse:
        def __init__(self, data):
            self._data = data
        def raise_for_status(self):
            pass
        def json(self):
            return {"rows": self._data}

    def _fake_post(url, json, timeout):
        return _FakeResponse([
            {"day": "2026-08-01", "risk_tier": "HIGH",     "count": 2},
            {"day": "2026-08-01", "risk_tier": "MEDIUM",   "count": 1},
            {"day": "2026-08-01", "risk_tier": "LOW",      "count": 1},
            {"day": "2026-08-05", "risk_tier": "HIGH",     "count": 3},
            {"day": "2026-08-05", "risk_tier": "MEDIUM",   "count": 1},
            {"day": "2026-08-10", "risk_tier": "HIGH",     "count": 2},
            {"day": "2026-08-10", "risk_tier": "MEDIUM",   "count": 1},
            {"day": "2026-08-10", "risk_tier": "LOW",      "count": 1},
        ])

    with patch("requests.post", _fake_post):
        client = TestClient(app)

        # Test 1: distribution trend (no filter)
        r1 = client.get("/api/risk-tier-distribution-trend/distribution?days=30")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert "points" in d1, f"Missing points: {d1}"
        assert len(d1["points"]) > 0, "Expected at least one distribution point"
        assert d1["lookback_days"] == 30
        # Check that tiers are present in at least one point
        all_tiers = set()
        for pt in d1["points"]:
            for t in pt["distribution"]:
                all_tiers.add(t["tier"])
        assert "HIGH" in all_tiers, f"Expected HIGH in tiers: {all_tiers}"
        assert "MEDIUM" in all_tiers, f"Expected MEDIUM in tiers: {all_tiers}"
        assert "LOW" in all_tiers, f"Expected LOW in tiers: {all_tiers}"

        # Test 2: distribution trend filtered by tier
        r2 = client.get("/api/risk-tier-distribution-trend/distribution?days=30&tier=HIGH")
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert "points" in d2
        for pt in d2["points"]:
            tier_names = {t["tier"] for t in pt["distribution"]}
            # HIGH should be in every point when filtered
            assert "HIGH" in tier_names, f"Expected HIGH in {tier_names} for {pt['date']}"

        # Test 3: per-server trend
        r3 = client.get("/api/risk-tier-distribution-trend/servers/srv1?days=30")
        assert r3.status_code == 200, r3.text
        d3 = r3.json()
        assert d3["server_id"] == "srv1"
        assert d3["server_name"] == "Alpha"
        assert isinstance(d3["trend"], list)

        # Test 4: server not in mesh history returns empty trend
        r4 = client.get("/api/risk-tier-distribution-trend/servers/nonexistent?days=30")
        assert r4.status_code == 200, r4.text
        d4 = r4.json()
        assert d4["server_id"] == "nonexistent"
        assert d4["trend"] == []

    print("PASS")
    sys.exit(0)
