from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class RiskTrendPoint(BaseModel):
    scored_at: datetime
    p_top: float
    risk_tier: str
    axis_count: int

def get_risk_tier(p_top: float) -> str:
    if p_top > 75:
        return "TRUSTED_GENERAL"
    elif p_top > 60:
        return "TRUSTED_RESEARCH"
    elif p_top > 45:
        return "ENTERPRISE_CONTROLLED"
    elif p_top > 30:
        return "CAUTION_LIMITED"
    elif p_top > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

@router.get("/servers/{server_id}/risk-trend", response_model=List[RiskTrendPoint])
def get_server_risk_trend(server_id: str, limit: int = 30, db: Session = Depends(get_session)) -> List[RiskTrendPoint]:
    if limit > 200:
        limit = 200

    results = db.query(
        McpLlmAxisScores.scored_at,
        McpLlmAxisScores.p_top,
        func.count(McpLlmAxisScores.axis_name).label("axis_count")
    ).filter(
        McpLlmAxisScores.server_id == server_id,
        McpLlmAxisScores.axis_name == 'overall_risk'
    ).group_by(
        McpLlmAxisScores.scored_at,
        McpLlmAxisScores.p_top
    ).order_by(
        desc(McpLlmAxisScores.scored_at)
    ).limit(limit).all()

    trend_points = []
    for row in results:
        trend_points.append(
            RiskTrendPoint(
                scored_at=row.scored_at,
                p_top=row.p_top,
                risk_tier=get_risk_tier(row.p_top),
                axis_count=row.axis_count
            )
        )

    return sorted(trend_points, key=lambda x: x.scored_at)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpLlmAxisScores
    from app.main import app

    # Override the database session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Add test data
    with TestSession() as session:
        test_data = [
            McpLlmAxisScores(
                server_id="srv1",
                axis_name="overall_risk",
                p_top=80.0,
                scored_at=datetime(2023, 1, 1, 0, 0, 0)
            ),
            McpLlmAxisScores(
                server_id="srv1",
                axis_name="overall_risk",
                p_top=70.0,
                scored_at=datetime(2023, 1, 2, 0, 0, 0)
            ),
            McpLlmAxisScores(
                server_id="srv1",
                axis_name="overall_risk",
                p_top=65.0,
                scored_at=datetime(2023, 1, 3, 0, 0, 0)
            ),
            McpLlmAxisScores(
                server_id="srv1",
                axis_name="overall_risk",
                p_top=50.0,
                scored_at=datetime(2023, 1, 4, 0, 0, 0)
            ),
            McpLlmAxisScores(
                server_id="srv1",
                axis_name="overall_risk",
                p_top=40.0,
                scored_at=datetime(2023, 1, 5, 0, 0, 0)
            )
        ]
        session.add_all(test_data)
        session.commit()

    client = TestClient(app)

    response = client.get("/servers/srv1/risk-trend")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    assert all(isinstance(item["p_top"], float) for item in data)
    assert all(item["risk_tier"] in ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"] for item in data)
    assert data == sorted(data, key=lambda x: x["scored_at"])

    print("PASS")