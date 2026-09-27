# deps: fastapi, sqlalchemy
"""Risk tier trend -- per-day tier counts over a lookback window."""
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_trend"])


def _tier(score: float) -> str:
    if score is None:
        return "unknown"
    if score >= 0.9:
        return "high"
    if score >= 0.7:
        return "medium"
    if score >= 0.5:
        return "low"
    return "minimal"


@router.get("/risk/trend")
def risk_trend(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict:
    """Return per-day risk-tier server counts for the lookback window.
    Returns a list of {date, tier, count} objects (the 'series' field)."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = session.execute(
        select(
            func.date(McpServerRegistry.updated_at).label("day"),
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .where(McpServerRegistry.updated_at >= cutoff)
        .group_by(func.date(McpServerRegistry.updated_at), McpServerRegistry.risk_tier)
        .order_by(func.date(McpServerRegistry.updated_at), McpServerRegistry.risk_tier)
    ).all()

    series = [
        {
            "date": str(r.day),
            "tier": r.risk_tier or "unknown",
            "count": r.cnt,
        }
        for r in rows
    ]

    return {"days": days, "series": series}


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def _override():
        return TestingSession()

    import app.db as _db
    _db.get_session = _override

    client = TestClient(router)

    # empty when no data
    r = client.get("/risk/trend?days=2")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "series" in data, f"Expected 'series' in response, got {data}"
    assert isinstance(data["series"], list), f"Expected list, got {type(data['series'])}"
    print("Self-test passed")
