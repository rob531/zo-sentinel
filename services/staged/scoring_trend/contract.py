from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/scoring/trend")

class TierCounts(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    ENTERPRISE_CONTROLLED: int = 0
    CAUTION_LIMITED: int = 0
    HIGH_RISK_ISOLATED: int = 0
    INSUFFICIENT: int = 0

class TrendSeriesItem(BaseModel):
    date: str
    tier_counts: TierCounts
    avg_p_top: float

class ScoringTrendResponse(BaseModel):
    days: int
    server_count: int
    series: List[TrendSeriesItem]

def get_scoring_trend(days: int, db: Session = Depends(get_session)) -> ScoringTrendResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Query to get scores grouped by date and risk tier
    subquery = (
        db.query(
            func.date(McpLlmAxisScore.scored_at).label('date'),
            McpLlmAxisScore.risk_tier,
            func.count(McpServerRegistry.id).label('server_count'),
            func.avg(McpLlmAxisScore.p_top).label('avg_p_top')
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.id)
        .filter(and_(
            McpLlmAxisScore.scored_at >= start_date,
            McpLlmAxisScore.scored_at <= end_date
        ))
        .group_by(func.date(McpLlmAxisScore.scored_at), McpLlmAxisScore.risk_tier)
        .subquery()
    )

    # Query to get the total server count
    server_count = (
        db.query(func.count(McpServerRegistry.id))
        .filter(McpServerRegistry.id.in_(
            db.query(McpLlmAxisScore.server_id)
            .filter(and_(
                McpLlmAxisScore.scored_at >= start_date,
                McpLlmAxisScore.scored_at <= end_date
            ))
        ))
        .scalar()
    )

    # Query to get the aggregated data
    results = (
        db.query(
            subquery.c.date,
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'TRUSTED_GENERAL', subquery.c.server_count),
                    else_=0
                )
            ).label('TRUSTED_GENERAL'),
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'TRUSTED_RESEARCH', subquery.c.server_count),
                    else_=0
                )
            ).label('TRUSTED_RESEARCH'),
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'ENTERPRISE_CONTROLLED', subquery.c.server_count),
                    else_=0
                )
            ).label('ENTERPRISE_CONTROLLED'),
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'CAUTION_LIMITED', subquery.c.server_count),
                    else_=0
                )
            ).label('CAUTION_LIMITED'),
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'HIGH_RISK_ISOLATED', subquery.c.server_count),
                    else_=0
                )
            ).label('HIGH_RISK_ISOLATED'),
            func.sum(
                func.case(
                    (subquery.c.risk_tier == 'INSUFFICIENT', subquery.c.server_count),
                    else_=0
                )
            ).label('INSUFFICIENT'),
            func.avg(subquery.c.avg_p_top).label('avg_p_top')
        )
        .group_by(subquery.c.date)
        .order_by(subquery.c.date)
        .all()
    )

    series = []
    for row in results:
        series.append({
            'date': row.date.strftime('%Y-%m-%d'),
            'tier_counts': {
                'TRUSTED_GENERAL': row.TRUSTED_GENERAL,
                'TRUSTED_RESEARCH': row.TRUSTED_RESEARCH,
                'ENTERPRISE_CONTROLLED': row.ENTERPRISE_CONTROLLED,
                'CAUTION_LIMITED': row.CAUTION_LIMITED,
                'HIGH_RISK_ISOLATED': row.HIGH_RISK_ISOLATED,
                'INSUFFICIENT': row.INSUFFICIENT
            },
            'avg_p_top': float(row.avg_p_top) if row.avg_p_top is not None else 0.0
        })

    return ScoringTrendResponse(
        days=days,
        server_count=server_count if server_count is not None else 0,
        series=series
    )

@router.get("/api/scoring/trend", response_model=ScoringTrendResponse)
async def scoring_trend(days: int, db: Session = Depends(get_session)):
    return get_scoring_trend(days, db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        # Create test servers
        server1 = McpServerRegistry(id=1, hostname="server1.example.com")
        server2 = McpServerRegistry(id=2, hostname="server2.example.com")
        server3 = McpServerRegistry(id=3, hostname="server3.example.com")
        db.add_all([server1, server2, server3])

        # Create test scores
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        scores = [
            McpLlmAxisScore(
                server_id=1,
                scored_at=now,
                risk_tier="TRUSTED_GENERAL",
                p_top=0.9
            ),
            McpLlmAxisScore(
                server_id=2,
                scored_at=now,
                risk_tier="TRUSTED_RESEARCH",
                p_top=0.8
            ),
            McpLlmAxisScore(
                server_id=3,
                scored_at=now,
                risk_tier="ENTERPRISE_CONTROLLED",
                p_top=0.7
            ),
            McpLlmAxisScore(
                server_id=1,
                scored_at=yesterday,
                risk_tier="TRUSTED_GENERAL",
                p_top=0.85
            ),
            McpLlmAxisScore(
                server_id=2,
                scored_at=yesterday,
                risk_tier="CAUTION_LIMITED",
                p_top=0.6
            ),
            McpLlmAxisScore(
                server_id=3,
                scored_at=yesterday,
                risk_tier="HIGH_RISK_ISOLATED",
                p_top=0.4
            ),
            McpLlmAxisScore(
                server_id=1,
                scored_at=two_days_ago,
                risk_tier="TRUSTED_GENERAL",
                p_top=0.8
            ),
            McpLlmAxisScore(
                server_id=2,
                scored_at=two_days_ago,
                risk_tier="TRUSTED_RESEARCH",
                p_top=0.75
            ),
            McpLlmAxisScore(
                server_id=3,
                scored_at=two_days_ago,
                risk_tier="INSUFFICIENT",
                p_top=0.3
            )
        ]
        db.add_all(scores)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/scoring/trend?days=3")
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 3
    assert data["server_count"] == 3
    assert len(data["series"]) == 3
    assert data["series"][1]["tier_counts"]["TRUSTED_GENERAL"] >= 1

    print("PASS")