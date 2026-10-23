# services/staged/overview_dashboard/contract.py
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real data layer import (no stub DB)
from app.db import get_session

router = APIRouter()


class TierDistribution(BaseModel):
    tier: str
    count: int


class Summary(BaseModel):
    total_servers: int
    tier_distribution: List[TierDistribution]


class TrendSeriesItem(BaseModel):
    date: date
    tier: str
    count: int


class Trend(BaseModel):
    days: int
    series: List[TrendSeriesItem]


class DashboardResponse(BaseModel):
    summary: Summary
    trend: Trend


@router.get(
    "/api/overview/dashboard",
    response_model=DashboardResponse,
    tags=["overview_dashboard"],
)
def get_overview_dashboard(db: Session = Depends(get_session)):
    # ---- Summary ----
    summary_rows = db.execute(
        text(
            """
            SELECT tier, count
            FROM mcp_risk_tier_summary
            ORDER BY tier
            """
        )
    ).fetchall()

    tier_distribution = [
        TierDistribution(tier=row["tier"], count=row["count"]) for row in summary_rows
    ]
    total_servers = sum(item.count for item in tier_distribution)

    # ---- Trend ----
    trend_rows = db.execute(
        text(
            """
            SELECT date, tier, count
            FROM mcp_risk_tier_trend
            ORDER BY date, tier
            """
        )
    ).fetchall()

    series = [
        TrendSeriesItem(date=row["date"], tier=row["tier"], count=row["count"])
        for row in trend_rows
    ]
    days = len({row["date"] for row in trend_rows})

    return DashboardResponse(
        summary=Summary(total_servers=total_servers, tier_distribution=tier_distribution),
        trend=Trend(days=days, series=series),
    )


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.overview_dashboard.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine, Column, Date, Integer, String, MetaData, Table
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    # FastAPI app for the test
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite setup
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    metadata = MetaData()

    summary_tbl = Table(
        "mcp_risk_tier_summary",
        metadata,
        Column("tier", String, primary_key=True),
        Column("count", Integer, nullable=False),
    )
    trend_tbl = Table(
        "mcp_risk_tier_trend",
        metadata,
        Column("date", Date, primary_key=True),
        Column("tier", String, primary_key=True),
        Column("count", Integer, nullable=False),
    )
    metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)

    # Seed data
    with engine.begin() as conn:
        conn.execute(
            summary_tbl.insert(),
            [
                {"tier": "A", "count": 10},
                {"tier": "B", "count": 5},
            ],
        )
        conn.execute(
            trend_tbl.insert(),
            [
                {"date": date(2023, 1, 1), "tier": "A", "count": 6},
                {"date": date(2023, 1, 1), "tier": "B", "count": 4},
                {"date": date(2023, 1, 2), "tier": "A", "count": 7},
                {"date": date(2023, 1, 2), "tier": "B", "count": 3},
            ],
        )

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/overview/dashboard")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    expected = {
        "summary": {
            "total_servers": 15,
            "tier_distribution": [
                {"tier": "A", "count": 10},
                {"tier": "B", "count": 5},
            ],
        },
        "trend": {
            "days": 2,
            "series": [
                {"date": "2023-01-01", "tier": "A", "count": 6},
                {"date": "2023-01-01", "tier": "B", "count": 4},
                {"date": "2023-01-02", "tier": "A", "count": 7},
                {"date": "2023-01-02", "tier": "B", "count": 3},
            ],
        },
    }

    if resp.json() != expected:
        print("FAIL: response mismatch", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)