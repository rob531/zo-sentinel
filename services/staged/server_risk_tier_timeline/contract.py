from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

class TimelineEntry(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierTimelineResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[TimelineEntry]

def get_risk_tier_timeline(server_id: str, days: int = 30, db: Session = Depends(get_session)) -> RiskTierTimelineResponse:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    query = db.query(
        func.date(McpLlmAxisScore.scored_at).label('date'),
        McpServerRegistry.risk_tier.label('tier'),
        func.count().label('count')
    ).join(
        McpServerRegistry,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= cutoff_date
    ).group_by(
        func.date(McpLlmAxisScore.scored_at),
        McpServerRegistry.risk_tier
    ).order_by(
        func.date(McpLlmAxisScore.scored_at).asc()
    ).all()

    timeline = [
        TimelineEntry(
            date=entry.date.isoformat(),
            tier=entry.tier,
            count=entry.count
        ) for entry in query
    ]

    return RiskTierTimelineResponse(
        server_id=server_id,
        days=days,
        timeline=timeline
    )

@router.get("/server/{server_id}/risk_tier_timeline", response_model=RiskTierTimelineResponse)
def server_risk_tier_timeline(
    server_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session)
):
    try:
        return get_risk_tier_timeline(server_id, days, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as db:
        server = McpServerRegistry(
            server_id="srv1",
            risk_tier="high"
        )
        db.add(server)

        scores = [
            McpLlmAxisScore(
                server_id="srv1",
                axis_name="overall_risk",
                scored_at=datetime.utcnow() - timedelta(days=2),
                score=0.9
            ),
            McpLlmAxisScore(
                server_id="srv1",
                axis_name="overall_risk",
                scored_at=datetime.utcnow() - timedelta(days=1),
                score=0.7
            ),
            McpLlmAxisScore(
                server_id="srv1",
                axis_name="overall_risk",
                scored_at=datetime.utcnow(),
                score=0.5
            )
        ]
        db.add_all(scores)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/server/srv1/risk_tier_timeline?days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["timeline"]) == 3
    assert any(entry["date"] == (datetime.utcnow() - timedelta(days=2)).date().isoformat() and entry["count"] == 1 for entry in data["timeline"])

    print("PASS")