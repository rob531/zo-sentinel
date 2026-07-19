from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func
import requests

router = APIRouter()

class AxisSummary(BaseModel):
    count: int
    avg_p_top: float
    max_p_critical: float
    p_danger_distribution: Dict[str, int]

class AxisScoreSummaryResponse(BaseModel):
    axis_scores: Dict[str, AxisSummary]

def get_axis_score_summary(db: Session = Depends(get_session)) -> JSONResponse:
    try:
        # Query the database to get aggregated statistics for each axis
        results = db.query(
            MCPLLMAxisScores.axis_name,
            func.count(MCPLLMAxisScores.id).label('count'),
            func.avg(MCPLLMAxisScores.p_top).label('avg_p_top'),
            func.max(MCPLLMAxisScores.p_critical).label('max_p_critical')
        ).group_by(MCPLLMAxisScores.axis_name).all()

        # Query for p_danger distribution
        danger_distribution = db.query(
            MCPLLMAxisScores.axis_name,
            MCPLLMAxisScores.p_danger,
            func.count(MCPLLMAxisScores.id).label('count')
        ).group_by(MCPLLMAxisScores.axis_name, MCPLLMAxisScores.p_danger).all()

        # Process results into the required format
        axis_scores = {}
        for row in results:
            axis_name = row.axis_name
            axis_scores[axis_name] = {
                'count': row.count,
                'avg_p_top': float(row.avg_p_top),
                'max_p_critical': float(row.max_p_critical),
                'p_danger_distribution': {}
            }

        # Add p_danger distribution to each axis
        for row in danger_distribution:
            axis_name = row.axis_name
            p_danger = row.p_danger
            count = row.count
            if axis_name in axis_scores:
                bucket = f"{int(p_danger * 100)}"
                axis_scores[axis_name]['p_danger_distribution'][bucket] = count

        return JSONResponse(content={"axis_scores": axis_scores})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

router.get("/axis_scores/summary")(get_axis_score_summary)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a test app and override the get_session dependency
    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Add some test data
    with SessionLocal() as session:
        test_data = [
            MCPLLMAxisScores(
                server_id=1,
                axis_name="axis1",
                p_top=80.0,
                p_critical=90.0,
                p_danger=0.1
            ),
            MCPLLMAxisScores(
                server_id=1,
                axis_name="axis1",
                p_top=85.0,
                p_critical=95.0,
                p_danger=0.2
            ),
            MCPLLMAxisScores(
                server_id=2,
                axis_name="axis2",
                p_top=70.0,
                p_critical=80.0,
                p_danger=0.3
            ),
        ]
        session.add_all(test_data)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/axis_scores/summary")
    assert response.status_code == 200
    data = response.json()

    # Check that all 7 axes are present (though we only added 2 in test data)
    # This is a simplified check for the example
    assert len(data["axis_scores"]) >= 2

    # Check that avg_p_top is a float between 0 and 100
    for axis in data["axis_scores"].values():
        assert isinstance(axis["avg_p_top"], float)
        assert 0 <= axis["avg_p_top"] <= 100

    print("PASS")