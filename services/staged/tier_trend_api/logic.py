"""logic.py -- tier trend data and API logic for the dashboard."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class TierTrendPoint(BaseModel):
    date: str
    tier: str
    count: int


class TierTrendResponse(BaseModel):
    days: int
    series: List[TierTrendPoint]
    totals: Dict[str, int]


router = APIRouter(prefix="/api")


def _normalise_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def tier_trend(db: Session, days: int = 30) -> Dict[str, object]:
    """Return registry tier counts for each date in the requested window."""
    days = max(1, min(365, int(days)))
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    end_date = start_date + timedelta(days=days)
    start_at = datetime.combine(start_date, time.min)
    end_at = datetime.combine(end_date, time.min)

    date_filter = (
        McpServerRegistry.last_assessed >= start_at,
        McpServerRegistry.last_assessed < end_at,
    )

    date_rows = db.execute(
        select(distinct(func.date(McpServerRegistry.last_assessed)))
        .where(*date_filter)
    ).all()
    dates = sorted({_normalise_date(row[0]) for row in date_rows if row[0] is not None})

    tier_rows = db.execute(
        select(McpServerRegistry.risk_tier)
        .where(*date_filter)
        .distinct()
    ).all()
    tiers = sorted({str(row[0]) for row in tier_rows if row[0] is not None})

    count_rows = db.execute(
        select(
            func.date(McpServerRegistry.last_assessed).label("assessment_date"),
            McpServerRegistry.risk_tier.label("tier"),
            func.count(McpServerRegistry.server_id).label("server_count"),
        )
        .where(*date_filter)
        .group_by(
            func.date(McpServerRegistry.last_assessed),
            McpServerRegistry.risk_tier,
        )
    ).all()

    counts: Dict[tuple[str, str], int] = {}
    for assessment_date, tier, server_count in count_rows:
        if assessment_date is None or tier is None:
            continue
        key = (_normalise_date(assessment_date), str(tier))
        counts[key] = int(server_count)

    series = [
        {
            "date": assessment_date,
            "tier": tier,
            "count": counts.get((assessment_date, tier), 0),
        }
        for assessment_date in dates
        for tier in tiers
    ]

    totals = {tier: 0 for tier in tiers}
    for (_, tier), count in counts.items():
        if tier in totals:
            totals[tier] += count

    return {"days": days, "series": series, "totals": totals}


get_tier_trend = tier_trend


@router.get("/dashboard/tier-trend", response_model=TierTrendResponse)
def tier_trend_endpoint(
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    return tier_trend(db, days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="selftest-1",
                    risk_tier="LOW",
                    last_assessed=now - timedelta(days=1),
                    registry_source="selftest",
                ),
                McpServerRegistry(
                    server_id="selftest-2",
                    risk_tier="MEDIUM",
                    last_assessed=now - timedelta(days=1),
                    registry_source="selftest",
                ),
                McpServerRegistry(
                    server_id="selftest-3",
                    risk_tier="LOW",
                    last_assessed=now - timedelta(days=2),
                    registry_source="selftest",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/dashboard/tier-trend", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series"]) == 4
    assert all(point["tier"] in {"LOW", "MEDIUM"} for point in payload["series"])
    print("PASS")