# deps: fastapi, sqlalchemy, requests
"""Router for risk_tier_trend_by_source service.
Provides risk tier trend summaries over time, grouped by source.
Uses APP tables via SQLAlchemy and MESH tables via write_service.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["risk_tier_trend_by_source"])


class TrendPoint(BaseModel):
    timestamp: str
    risk_tier: float
    risk_label: str


class SourceTrend(BaseModel):
    source: str
    server_count: int
    current_tier: Optional[float]
    trend_points: List[TrendPoint]


class RiskTierTrendResponse(BaseModel):
    sources: List[SourceTrend]
    lookback_days: int
    generated_at: str


def _risk_label(tier: float) -> str:
    """Convert numeric tier to label."""
    if tier >= 80:
        return "CRITICAL"
    elif tier >= 60:
        return "HIGH"
    elif tier >= 40:
        return "MEDIUM"
    elif tier >= 20:
        return "LOW"
    return "MINIMAL"


def get_source_trends(
    db: Session,
    source: Optional[str] = None,
    days_back: int = 30,
) -> List[SourceTrend]:
    """Query APP + MESH tables for risk tier trends by source."""
    from app.models import MCPServerRegistry

    sources_to_query = []
    if source:
        sources_to_query.append(source)
    else:
        # Get all sources with registry entries
        servers = db.query(MCPServerRegistry.registry_source).distinct().all()
        sources_to_query = [s[0] for s in servers if s[0]]

    results: List[SourceTrend] = []
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"

    for src in sources_to_query:
        # Query APP table for server count
        server_count = db.query(MCPServerRegistry).filter(
            MCPServerRegistry.registry_source == src
        ).count()

        # Query MESH table for historical trend data via write_service
        import requests as _req
        trend_points: List[TrendPoint] = []
        current_tier: Optional[float] = None

        try:
            resp = _req.post(
                "http://127.0.0.1:8772/query",
                json={
                    "sql": (
                        "SELECT timestamp, risk_tier "
                        "FROM mcp_signal_scores "
                        "WHERE source_id = ? AND timestamp > ? "
                        "ORDER BY timestamp ASC"
                    ),
                    "params": [src, cutoff],
                },
                timeout=10,
            )
            resp.raise_for_status()
            mesh_data = resp.json() or []

            for row in mesh_data:
                tier = float(row.get("risk_tier", 50))
                if current_tier is None:
                    current_tier = tier
                trend_points.append(TrendPoint(
                    timestamp=row.get("timestamp", ""),
                    risk_tier=tier,
                    risk_label=_risk_label(tier),
                ))

        except _req.RequestException:
            pass  # No mesh data available for this source

        if not trend_points and server_count == 0:
            continue  # Skip sources with no data

        results.append(SourceTrend(
            source=src,
            server_count=server_count,
            current_tier=current_tier,
            trend_points=trend_points,
        ))

    return results


@router.get("/risk_tier_trend_by_source", response_model=RiskTierTrendResponse)
def get_risk_tier_trends(
    source: Optional[str] = Query(None, description="Filter by specific source"),
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> RiskTierTrendResponse:
    """
    Return risk tier trend data grouped by source.
    Combines APP table server counts with MESH table historical scores.
    """
    trends = get_source_trends(db, source=source, days_back=days)
    return RiskTierTrendResponse(
        sources=trends,
        lookback_days=days,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Override data layer for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    session = TestSession()
    from app.models import MCPServerRegistry, Org

    test_org = Org(id="org-1", name="Test Org")
    session.add(test_org)

    servers = [
        MCPServerRegistry(
            server_id="srv-1",
            name="Server One",
            registry_source="npm",
            risk_tier="MEDIUM",
        ),
        MCPServerRegistry(
            server_id="srv-2",
            name="Server Two",
            registry_source="npm",
            risk_tier="HIGH",
        ),
        MCPServerRegistry(
            server_id="srv-3",
            name="Server Three",
            registry_source="github",
            risk_tier="LOW",
        ),
    ]
    session.add_all(servers)
    session.commit()

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test filtered by source
    resp = client.get("/api/risk_tier_trend_by_source", params={"source": "npm", "days": 7})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "sources" in data
    assert data["lookback_days"] == 7
    print(f"Response keys: {list(data.keys())}")

    # Test all sources
    resp2 = client.get("/api/risk_tier_trend_by_source", params={"days": 14})
    assert resp2.status_code == 200
    data2 = resp2.json()
    print(f"Total sources: {len(data2['sources'])}")

    print("Self-test passed")
    sys.exit(0)
