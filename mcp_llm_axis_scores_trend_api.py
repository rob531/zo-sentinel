from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class DataPoint(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    p_danger: float
    label_index: int

class AxisTrend(BaseModel):
    axis_name: str
    label: str
    data_points: List[DataPoint]

class ServerTrendResponse(BaseModel):
    server_id: int
    axes: List[AxisTrend]

@router.get("/servers/{server_id}/axis-scores/trend", response_model=ServerTrendResponse)
async def get_axis_scores_trend(
    server_id: int,
    days: Optional[int] = 30,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    session: Session = Depends(get_session)
):
    # Parse date filters
    if from_date:
        from_date = datetime.fromisoformat(from_date)
    else:
        from_date = datetime.now() - timedelta(days=days)

    if to_date:
        to_date = datetime.fromisoformat(to_date)
    else:
        to_date = datetime.now()

    # Query historical axis scores
    query = session.query(
        McpLlmAxisScores.server_id,
        McpLlmAxisScores.axis_name,
        McpLlmAxisScores.label,
        McpLlmAxisScores.scored_at,
        McpLlmAxisScores.p_top,
        McpLlmAxisScores.p_critical,
        McpLlmAxisScores.p_danger,
        McpLlmAxisScores.label_index
    ).filter(
        McpLlmAxisScores.server_id == server_id,
        and_(
            McpLlmAxisScores.scored_at >= from_date,
            McpLlmAxisScores.scored_at <= to_date
        )
    ).order_by(
        McpLlmAxisScores.scored_at.asc()
    ).all()

    if not query:
        raise HTTPException(status_code=404, detail="No axis scores found for the given server and date range")

    # Group by axis_name
    axes_dict = {}
    for row in query:
        if row.axis_name not in axes_dict:
            axes_dict[row.axis_name] = {
                "axis_name": row.axis_name,
                "label": row.label,
                "data_points": []
            }
        axes_dict[row.axis_name]["data_points"].append({
            "scored_at": row.scored_at,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "label_index": row.label_index
        })

    # Convert to response model
    axes = list(axes_dict.values())
    return ServerTrendResponse(server_id=server_id, axes=axes)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_app = FastAPI()
    test_app.include_router(router)

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # Override dependency
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with TestingSessionLocal() as session:
        test_data = [
            McpLlmAxisScores(
                server_id=1,
                axis_name="axis1",
                label="label1",
                scored_at=datetime.now() - timedelta(days=2),
                p_top=0.8,
                p_critical=0.1,
                p_danger=0.1,
                label_index=1
            ),
            McpLlmAxisScores(
                server_id=1,
                axis_name="axis1",
                label="label1",
                scored_at=datetime.now() - timedelta(days=1),
                p_top=0.7,
                p_critical=0.2,
                p_danger=0.1,
                label_index=1
            ),
            McpLlmAxisScores(
                server_id=1,
                axis_name="axis2",
                label="label2",
                scored_at=datetime.now() - timedelta(days=2),
                p_top=0.6,
                p_critical=0.3,
                p_danger=0.1,
                label_index=2
            ),
            McpLlmAxisScores(
                server_id=1,
                axis_name="axis2",
                label="label2",
                scored_at=datetime.now() - timedelta(days=1),
                p_top=0.5,
                p_critical=0.3,
                p_danger=0.2,
                label_index=2
            ),
            McpLlmAxisScores(
                server_id=1,
                axis_name="axis1",
                label="label1",
                scored_at=datetime.now() - timedelta(days=3),
                p_top=0.9,
                p_critical=0.05,
                p_danger=0.05,
                label_index=1
            )
        ]
        session.add_all(test_data)
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)

    # Test without date filters
    response = client.get("/servers/1/axis-scores/trend")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 2
    for axis in data["axes"]:
        assert len(axis["data_points"]) > 0

    # Test with date filters
    from_date = (datetime.now() - timedelta(days=2)).isoformat()
    response = client.get(f"/servers/1/axis-scores/trend?from_date={from_date}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 2
    for axis in data["axes"]:
        assert len(axis["data_points"]) == 2

    print("PASS")