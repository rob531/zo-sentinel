from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/risk", tags=["risk"])


class TierDistributionResponse(BaseModel):
    tier: str
    count: int
    date: str


class TierDistributionResult(BaseModel):
    data: list[TierDistributionResponse]


@router.get("/tier-distribution-over-time", response_model=TierDistributionResult)
def tier_distribution_over_time(
    start_date: str | None = None,
    end_date: str | None = None,
    session: Session = Depends(get_session),
) -> TierDistributionResult:
    """
    Returns risk tier distribution counts aggregated by day.
    """
    date_filter = ""
    params = {}
    if start_date:
        date_filter += " AND last_assessed >= :start_date"
        params["start_date"] = start_date
    if end_date:
        date_filter += " AND last_assessed <= :end_date"
        params["end_date"] = end_date

    sql = f"""
        SELECT 
            risk_tier,
            DATE(last_assessed) as assessment_date,
            COUNT(*) as tier_count
        FROM mcp_server_registry
        WHERE risk_tier IS NOT NULL
        {date_filter}
        GROUP BY risk_tier, DATE(last_assessed)
        ORDER BY assessment_date, risk_tier
    """

    result = session.execute(select(
        func.date(McpServerRegistry.last_assessed).label("assessment_date"),
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label("tier_count"),
    ).where(
        McpServerRegistry.risk_tier.isnot(None)
    ).group_by(
        func.date(McpServerRegistry.last_assessed),
        McpServerRegistry.risk_tier
    ).order_by(
        func.date(McpServerRegistry.last_assessed),
        McpServerRegistry.risk_tier
    ))

    rows = []
    for row in result:
        rows.append(TierDistributionResponse(
            tier=row.risk_tier,
            count=row.tier_count,
            date=str(row.assessment_date),
        ))

    return TierDistributionResult(data=rows)


if __name__ == "__main__":
    from datetime import datetime, timedelta
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    with TestingSessionLocal() as db:
        entries = [
            McpServerRegistry(
                server_id="srv-001",
                risk_tier="high",
                last_assessed=datetime.combine(today, datetime.min.time()),
                name="Server One",
            ),
            McpServerRegistry(
                server_id="srv-002",
                risk_tier="high",
                last_assessed=datetime.combine(today, datetime.min.time()),
                name="Server Two",
            ),
            McpServerRegistry(
                server_id="srv-003",
                risk_tier="medium",
                last_assessed=datetime.combine(yesterday, datetime.min.time()),
                name="Server Three",
            ),
            McpServerRegistry(
                server_id="srv-004",
                risk_tier="low",
                last_assessed=datetime.combine(yesterday, datetime.min.time()),
                name="Server Four",
            ),
            McpServerRegistry(
                server_id="srv-005",
                risk_tier="critical",
                last_assessed=datetime.combine(two_days_ago, datetime.min.time()),
                name="Server Five",
            ),
        ]
        for entry in entries:
            db.add(entry)
        db.commit()

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/risk/tier-distribution-over-time")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()["data"]

    tiers_found = {item["tier"] for item in data}
    assert "critical" in tiers_found, f"Missing critical tier in {data}"
    assert "high" in tiers_found, f"Missing high tier in {data}"
    assert "medium" in tiers_found, f"Missing medium tier in {data}"
    assert "low" in tiers_found, f"Missing low tier in {data}"

    high_items = [item for item in data if item["tier"] == "high"]
    assert len(high_items) >= 1, f"Expected high tier count >= 1, got {high_items}"

    print("PASS")