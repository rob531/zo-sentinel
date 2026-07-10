from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

router = APIRouter()

class AxisDelta(BaseModel):
    axis_name: str
    start_p_top: float
    end_p_top: float
    delta: float

class RiskDeltaResponse(BaseModel):
    server_id: str
    start_risk_tier: str
    end_risk_tier: str
    tier_changed: bool
    axes: List[AxisDelta]
    overall_delta: float
    start_scored_at: datetime
    end_scored_at: datetime

def get_risk_tier(session: Session, server_id: str, scored_at: datetime) -> Optional[str]:
    return session.query(MCPServerRegistry.risk_tier).filter(
        MCPServerRegistry.server_id == server_id,
        MCPServerRegistry.scored_at <= scored_at
    ).order_by(MCPServerRegistry.scored_at.desc()).first()[0]

@router.get("/servers/{server_id}/risk-delta", response_model=RiskDeltaResponse)
async def get_server_risk_delta(
    server_id: str,
    start_date: datetime = Query(..., description="Start date in ISO 8601 format"),
    end_date: datetime = Query(..., description="End date in ISO 8601 format"),
    session: Session = Depends(get_session)
):
    if start_date >= end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    # Get start and end records
    start_record = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.scored_at >= start_date
    ).order_by(MCPLLMAxisScores.scored_at.asc()).first()

    end_record = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.scored_at <= end_date
    ).order_by(MCPLLMAxisScores.scored_at.desc()).first()

    if not start_record or not end_record or start_record == end_record:
        raise HTTPException(status_code=404, detail="Not enough scoring records in the specified time window")

    # Get risk tiers
    start_risk_tier = get_risk_tier(session, server_id, start_record.scored_at)
    end_risk_tier = get_risk_tier(session, server_id, end_record.scored_at)

    # Calculate axis deltas
    axes = []
    overall_delta = 0.0

    for axis in start_record.axes:
        start_p_top = axis.p_top
        end_p_top = next((a.p_top for a in end_record.axes if a.axis_name == axis.axis_name), 0.0)
        delta = end_p_top - start_p_top
        overall_delta += abs(delta)
        axes.append(AxisDelta(
            axis_name=axis.axis_name,
            start_p_top=start_p_top,
            end_p_top=end_p_top,
            delta=delta
        ))

    return RiskDeltaResponse(
        server_id=server_id,
        start_risk_tier=start_risk_tier,
        end_risk_tier=end_risk_tier,
        tier_changed=start_risk_tier != end_risk_tier,
        axes=axes,
        overall_delta=overall_delta,
        start_scored_at=start_record.scored_at,
        end_scored_at=end_record.scored_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the session dependency for testing
    dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server_id = "test-server-123"

    # Add test records
    test_session.add(MCPServerRegistry(
        server_id=test_server_id,
        risk_tier="low",
        scored_at=datetime(2023, 1, 1)
    ))

    test_session.add(MCPLLMAxisScores(
        server_id=test_server_id,
        scored_at=datetime(2023, 1, 1),
        axes=[{"axis_name": "axis1", "p_top": 0.1}, {"axis_name": "axis2", "p_top": 0.2}]
    ))

    test_session.add(MCPServerRegistry(
        server_id=test_server_id,
        risk_tier="medium",
        scored_at=datetime(2023, 1, 2)
    ))

    test_session.add(MCPLLMAxisScores(
        server_id=test_server_id,
        scored_at=datetime(2023, 1, 2),
        axes=[{"axis_name": "axis1", "p_top": 0.3}, {"axis_name": "axis2", "p_top": 0.4}]
    ))

    test_session.commit()

    # Create FastAPI app for testing
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get(
        f"/servers/{test_server_id}/risk-delta",
        params={"start_date": "2023-01-01", "end_date": "2023-01-02"}
    )

    assert response.status_code == 200
    data = response.json()

    assert data["server_id"] == test_server_id
    assert data["start_risk_tier"] == "low"
    assert data["end_risk_tier"] == "medium"
    assert data["tier_changed"] == True
    assert len(data["axes"]) == 2
    assert data["axes"][0]["axis_name"] == "axis1"
    assert data["axes"][0]["start_p_top"] == 0.1
    assert data["axes"][0]["end_p_top"] == 0.3
    assert data["axes"][0]["delta"] == 0.2
    assert data["axes"][1]["axis_name"] == "axis2"
    assert data["axes"][1]["start_p_top"] == 0.2
    assert data["axes"][1]["end_p_top"] == 0.4
    assert data["axes"][1]["delta"] == 0.2
    assert data["overall_delta"] == 0.4
    assert data["start_scored_at"] == "2023-01-01T00:00:00"
    assert data["end_scored_at"] == "2023-01-02T00:00:00"

    print("PASS")