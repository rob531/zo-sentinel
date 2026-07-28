from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class ScoringResponse(BaseModel):
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str

def determine_risk_tier(axes: Dict[str, AxisScore]) -> str:
    critical_axes = ['critical_vulnerability', 'critical_misconfiguration']
    for axis in critical_axes:
        if axis in axes and axes[axis].p_top > 0.5:
            return 'CRITICAL'
    if axes['overall'].p_top > 0.8:
        return 'HIGH'
    elif axes['overall'].p_top > 0.6:
        return 'MEDIUM'
    elif axes['overall'].p_top > 0.4:
        return 'LOW'
    else:
        return 'MINIMAL'

@router.get("/scoring/consumer", response_model=ScoringResponse)
async def get_scoring_consumer(server_id: int, session: Session = Depends(get_session)) -> ScoringResponse:
    # Get all axis scores for the server
    axis_scores = session.query(
        McpLlmAxisScore.axis,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.criteria_version
    ).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")

    # Group scores by axis
    axes = {}
    criteria_version = None
    for score in axis_scores:
        axes[score.axis] = AxisScore(label=score.label, p_top=score.p_top)
        criteria_version = score.criteria_version

    if criteria_version is None:
        raise HTTPException(status_code=500, detail="Criteria version not found")

    # Calculate overall score
    overall_score = session.query(
        func.avg(McpLlmAxisScore.p_top).label('overall')
    ).filter(
        McpLlmAxisScore.server_id == server_id
    ).scalar()

    if overall_score is None:
        raise HTTPException(status_code=500, detail="Overall score not found")

    # Determine risk tier
    risk_tier = determine_risk_tier(axes)

    return ScoringResponse(
        axes=axes,
        overall=overall_score,
        risk_tier=risk_tier,
        criteria_version=criteria_version
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine, get_session
    from app.models import McpLlmAxisScore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server_id = 1
    test_scores = [
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="critical_vulnerability",
            label="Critical Vulnerability",
            p_top=0.6,
            criteria_version="v1.0"
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="critical_misconfiguration",
            label="Critical Misconfiguration",
            p_top=0.4,
            criteria_version="v1.0"
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="data_exposure",
            label="Data Exposure",
            p_top=0.7,
            criteria_version="v1.0"
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="access_control",
            label="Access Control",
            p_top=0.5,
            criteria_version="v1.0"
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="update_status",
            label="Update Status",
            p_top=0.3,
            criteria_version="v1.0"
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis="network_security",
            label="Network Security",
            p_top=0.8,
            criteria_version="v1.0"
        ),
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Create FastAPI app for testing
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/scoring/consumer?server_id={test_server_id}")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert len(data["axes"]) == 6
    assert "critical_vulnerability" in data["axes"]
    assert "critical_misconfiguration" in data["axes"]
    assert "data_exposure" in data["axes"]
    assert "access_control" in data["axes"]
    assert "update_status" in data["axes"]
    assert "network_security" in data["axes"]
    assert data["risk_tier"] == "CRITICAL"  # Due to critical_vulnerability > 0.5
    assert data["criteria_version"] == "v1.0"

    print("PASS")