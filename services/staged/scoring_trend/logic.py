from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import Depends
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class TierCounts(BaseModel):
    TRUSTED_GENERAL: int
    TRUSTED_RESEARCH: int
    ENTERPRISE_CONTROLLED: int
    CAUTION_LIMITED: int
    HIGH_RISK_ISOLATED: int
    INSUFFICIENT: int

class SeriesItem(BaseModel):
    date: str
    tier_counts: TierCounts
    avg_p_top: float

class ScoringTrendResponse(BaseModel):
    days: int
    server_count: int
    series: List[SeriesItem]

def get_scoring_trend(days: int, db: Session = Depends(get_session)) -> ScoringTrendResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    subquery = (
        db.query(
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.risk_tier,
            McpServerRegistry.id
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.id)
        .filter(McpLlmAxisScore.scored_at >= start_date)
        .subquery()
    )

    query = (
        db.query(
            func.date(func.timezone('UTC', subquery.c.scored_at)).label('date'),
            subquery.c.risk_tier,
            func.count(subquery.c.id).label('count'),
            func.avg(McpLlmAxisScore.p_top).label('avg_p_top')
        )
        .group_by(func.date(func.timezone('UTC', subquery.c.scored_at)), subquery.c.risk_tier)
        .order_by(func.date(func.timezone('UTC', subquery.c.scored_at)))
    )

    results = query.all()

    series = []
    date_to_tiers = {}
    date_to_avg = {}

    for row in results:
        date = row.date.strftime('%Y-%m-%d')
        if date not in date_to_tiers:
            date_to_tiers[date] = {
                'TRUSTED_GENERAL': 0,
                'TRUSTED_RESEARCH': 0,
                'ENTERPRISE_CONTROLLED': 0,
                'CAUTION_LIMITED': 0,
                'HIGH_RISK_ISOLATED': 0,
                'INSUFFICIENT': 0
            }
        date_to_tiers[date][row.risk_tier] = row.count
        date_to_avg[date] = row.avg_p_top

    for date, tiers in date_to_tiers.items():
        series.append(SeriesItem(
            date=date,
            tier_counts=TierCounts(**tiers),
            avg_p_top=date_to_avg.get(date, 0.0)
        ))

    server_count = db.query(func.count(McpServerRegistry.id)).scalar()

    return ScoringTrendResponse(
        days=days,
        server_count=server_count,
        series=series
    )

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: session

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    # Seed test data
    test_servers = [
        McpServerRegistry(id=1, hostname="server1"),
        McpServerRegistry(id=2, hostname="server2"),
        McpServerRegistry(id=3, hostname="server3")
    ]
    session.add_all(test_servers)

    test_scores = [
        McpLlmAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 1),
            risk_tier="TRUSTED_GENERAL",
            p_top=0.9
        ),
        McpLlmAxisScore(
            server_id=2,
            scored_at=datetime(2023, 1, 1),
            risk_tier="TRUSTED_RESEARCH",
            p_top=0.8
        ),
        McpLlmAxisScore(
            server_id=3,
            scored_at=datetime(2023, 1, 1),
            risk_tier="ENTERPRISE_CONTROLLED",
            p_top=0.7
        ),
        McpLlmAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 2),
            risk_tier="TRUSTED_GENERAL",
            p_top=0.95
        ),
        McpLlmAxisScore(
            server_id=2,
            scored_at=datetime(2023, 1, 2),
            risk_tier="CAUTION_LIMITED",
            p_top=0.6
        ),
        McpLlmAxisScore(
            server_id=3,
            scored_at=datetime(2023, 1, 2),
            risk_tier="HIGH_RISK_ISOLATED",
            p_top=0.4
        ),
        McpLlmAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 3),
            risk_tier="INSUFFICIENT",
            p_top=0.3
        ),
        McpLlmAxisScore(
            server_id=2,
            scored_at=datetime(2023, 1, 3),
            risk_tier="TRUSTED_GENERAL",
            p_top=0.85
        ),
        McpLlmAxisScore(
            server_id=3,
            scored_at=datetime(2023, 1, 3),
            risk_tier="ENTERPRISE_CONTROLLED",
            p_top=0.75
        )
    ]
    session.add_all(test_scores)
    session.commit()

    client = TestClient(app)

    response = client.get("/api/scoring/trend?days=3")
    assert response.status_code == 200
    data = response.json()
    assert data['series'][1]['tier_counts']['TRUSTED_GENERAL'] >= 1
    print("PASS")