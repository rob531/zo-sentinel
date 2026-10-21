import datetime
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real application imports (must not be stubbed)
# ----------------------------------------------------------------------
from app.db import Base, get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class TierTrendItem(BaseModel):
    date: datetime.date
    tier: str
    count: int


class StatisticsResponse(BaseModel):
    total_servers: int
    tier_counts: Dict[str, int]
    avg_confidence: float = Field(..., ge=0.0, le=1.0)
    tier_trend: List[TierTrendItem]

    @validator("avg_confidence", pre=True, always=True)
    def _coerce_none(cls, v):
        return v or 0.0


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/verdicts/statistics",
    response_model=StatisticsResponse,
    name="verdict_statistics",
)
def get_verdict_statistics(
    org_id: Optional[int] = Query(
        None,
        description="Filter by organization ID (currently unused – placeholder for future support)",
    ),
    start_date: Optional[datetime.date] = Query(
        None, description="Inclusive start date for trend aggregation"
    ),
    end_date: Optional[datetime.date] = Query(
        None, description="Inclusive end date for trend aggregation"
    ),
    session: Session = Depends(get_session),
):
    """
    Return aggregated verdict statistics.

    The implementation uses the authoritative PostgreSQL tables via the
    application's SQLAlchemy session.  All queries are fully parameterised
    through SQLAlchemy's expression language.
    """
    # ------------------------------------------------------------------
    # Core aggregates from McpServerRegistry
    # ------------------------------------------------------------------
    total_q = select(func.count(McpServerRegistry.server_id))
    total_servers = session.execute(total_q).scalar_one()

    tier_counts_q = (
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    )
    tier_counts_raw = session.execute(tier_counts_q).all()
    tier_counts = {tier: cnt for tier, cnt in tier_counts_raw}

    avg_conf_q = select(func.avg(McpServerRegistry.confidence))
    avg_confidence = session.execute(avg_conf_q).scalar_one()
    avg_confidence = float(avg_confidence) if avg_confidence is not None else 0.0

    # ------------------------------------------------------------------
    # Trend aggregation (by date and tier)
    # ------------------------------------------------------------------
    date_col = func.date(McpServerRegistry.first_seen).label("date")
    trend_q = (
        select(
            date_col,
            McpServerRegistry.risk_tier.label("tier"),
            func.count().label("count"),
        )
        .group_by(date_col, McpServerRegistry.risk_tier)
        .order_by(date_col)
    )

    if start_date:
        trend_q = trend_q.where(date_col >= start_date)
    if end_date:
        trend_q = trend_q.where(date_col <= end_date)

    trend_rows = session.execute(trend_q).all()
    tier_trend = [
        TierTrendItem(date=row.date, tier=row.tier, count=row.count) for row in trend_rows
    ]

    # ------------------------------------------------------------------
    # Return the assembled response
    # ------------------------------------------------------------------
    return StatisticsResponse(
        total_servers=total_servers,
        tier_counts=tier_counts,
        avg_confidence=avg_confidence,
        tier_trend=tier_trend,
    )


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Build a minimal FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    TEST_DB_URL = "sqlite://"

    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed minimal data
    def seed():
        with TestingSessionLocal() as s:
            s.add_all(
                [
                    McpServerRegistry(
                        server_id="srv-1",
                        confidence=0.8,
                        risk_tier="TRUSTED_GENERAL",
                        first_seen=datetime.datetime(2023, 1, 1, 12, 0, 0),
                    ),
                    McpServerRegistry(
                        server_id="srv-2",
                        confidence=0.6,
                        risk_tier="TRUSTED_RESEARCH",
                        first_seen=datetime.datetime(2023, 1, 2, 12, 0, 0),
                    ),
                    McpServerRegistry(
                        server_id="srv-3",
                        confidence=0.9,
                        risk_tier="TRUSTED_GENERAL",
                        first_seen=datetime.datetime(2023, 1, 2, 13, 0, 0),
                    ),
                ]
            )
            s.commit()

    seed()

    # Dependency override
    def get_test_session() -> Session:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/verdicts/statistics")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic shape checks
    assert "total_servers" in data
    assert "tier_counts" in data
    assert "avg_confidence" in data
    assert "tier_trend" in data

    # Tier counts must contain the seeded tiers
    for tier in ("TRUSTED_GENERAL", "TRUSTED_RESEARCH"):
        assert tier in data["tier_counts"], f"Missing tier {tier}"

    # Avg confidence must be a float between 0 and 1
    avg = data["avg_confidence"]
    assert isinstance(avg, float) and 0.0 <= avg <= 1.0, f"Bad avg_confidence {avg}"

    # Trend must be a list
    assert isinstance(data["tier_trend"], list)

    print("PASS")
    raise SystemExit(0)