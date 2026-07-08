from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPLLMAxisScores
import requests
from sqlalchemy.orm import Session

router = APIRouter()

class RiskTimelineEntry(BaseModel):
    date: str
    overall_risk_score: float
    risk_tier: str
    axis_breakdown: Dict[str, float]

def get_server_risk_timeline(server_id: str, days: int = 30) -> List[Dict]:
    session: Session = Depends(get_session)

    # Calculate the start date for the query
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Query the database for the risk scores
    query = f"""
    SELECT
        scored_at::date as date,
        overall_risk_score,
        risk_tier,
        jsonb_build_object(
            'technical', technical_risk_score,
            'operational', operational_risk_score,
            'strategic', strategic_risk_score,
            'compliance', compliance_risk_score
        ) as axis_breakdown
    FROM mcp_llm_axis_scores
    WHERE server_id = '{server_id}'
    AND scored_at >= '{start_date}'
    ORDER BY scored_at
    """

    try:
        response = requests.get('http://127.0.0.1:8772/query', params={'q': query})
        response.raise_for_status()
        data = response.json()

        # Format the response
        timeline = []
        for row in data:
            timeline.append({
                'date': row['date'],
                'overall_risk_score': row['overall_risk_score'],
                'risk_tier': row['risk_tier'],
                'axis_breakdown': row['axis_breakdown']
            })

        return timeline
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/{server_id}/risk-timeline", response_model=List[RiskTimelineEntry])
async def server_risk_timeline(
    server_id: str,
    days: int = 30,
    session: Session = Depends(get_session)
):
    return get_server_risk_timeline(server_id, days)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Mock data setup
    mock_data = [
        {
            "server_id": "test_server",
            "scored_at": "2023-01-01",
            "overall_risk_score": 0.8,
            "risk_tier": "high",
            "technical_risk_score": 0.7,
            "operational_risk_score": 0.8,
            "strategic_risk_score": 0.9,
            "compliance_risk_score": 0.7
        },
        {
            "server_id": "test_server",
            "scored_at": "2023-01-02",
            "overall_risk_score": 0.6,
            "risk_tier": "medium",
            "technical_risk_score": 0.6,
            "operational_risk_score": 0.5,
            "strategic_risk_score": 0.7,
            "compliance_risk_score": 0.6
        },
        {
            "server_id": "test_server",
            "scored_at": "2023-01-03",
            "overall_risk_score": 0.4,
            "risk_tier": "low",
            "technical_risk_score": 0.4,
            "operational_risk_score": 0.3,
            "strategic_risk_score": 0.5,
            "compliance_risk_score": 0.4
        }
    ]

    # Mock write_service response
    def mock_get_session():
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()
        for data in mock_data:
            session.execute(
                MCPLLMAxisScores.__table__.insert().values(**data)
            )
        session.commit()
        return session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = mock_get_session

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/test_server/risk-timeline")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    for entry in data:
        assert 'date' in entry
        assert 'overall_risk_score' in entry
        assert 'risk_tier' in entry
        assert 'axis_breakdown' in entry

    print("PASS")