from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/risk")

class TrendPoint(BaseModel):
    date: str
    avg_score: float
    max_score: float
    min_score: float

class AxisTrendResponse(BaseModel):
    days: int
    axis_name: str
    series: List[TrendPoint]

VALID_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface"
]

@router.get("/axis_trend", response_model=AxisTrendResponse)
async def get_axis_trend(
    axis_name: str = Query(..., description="Risk axis name"),
    days: int = Query(..., description="Number of days to look back"),
    session: Session = Depends(get_session)
):
    if axis_name not in VALID_AXES:
        raise HTTPException(status_code=400, detail="Invalid axis_name")

    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be > 0")

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    query = session.query(
        McpLlmAxisScore.score_date,
        func.avg(McpLlmAxisScore.score).label("avg_score"),
        func.max(McpLlmAxisScore.score).label("max_score"),
        func.min(McpLlmAxisScore.score).label("min_score")
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.id
    ).filter(
        and_(
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.score_date >= start_date,
            McpLlmAxisScore.score_date <= end_date
        )
    ).group_by(
        McpLlmAxisScore.score_date
    ).order_by(
        McpLlmAxisScore.score_date
    ).all()

    series = [
        TrendPoint(
            date=point.score_date.isoformat(),
            avg_score=float(point.avg_score),
            max_score=float(point.max_score),
            min_score=float(point.min_score)
        )
        for point in query
    ]

    return AxisTrendResponse(
        days=days,
        axis_name=axis_name,
        series=series
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(id=1, hostname="server1"),
        McpServerRegistry(id=2, hostname="server2"),
        McpLlmAxisScore(
            server_id=1,
            axis_name="overall_risk",
            score=0.8,
            score_date=datetime.utcnow() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="overall_risk",
            score=0.6,
            score_date=datetime.utcnow() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id=1,
            axis_name="overall_risk",
            score=0.7,
            score_date=datetime.utcnow() - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="overall_risk",
            score=0.5,
            score_date=datetime.utcnow() - timedelta(days=2)
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/axis_trend?axis_name=overall_risk&days=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) == 2
    assert data["series"][0]["avg_score"] == 0.7
    print("PASS")