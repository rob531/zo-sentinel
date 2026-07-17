from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class AxisScore(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    label: str

class AxisTimeline(BaseModel):
    overall_risk: List[AxisScore]
    auth_strength: List[AxisScore]
    capability_breadth: List[AxisScore]
    data_sensitivity: List[AxisScore]
    network_egress: List[AxisScore]
    maintainer_trust: List[AxisScore]
    exploit_surface: List[AxisScore]

class ServerAxisTimelineResponse(BaseModel):
    server_id: int
    axes: AxisTimeline

@router.get("/servers/{server_id}/axis-timeline", response_model=ServerAxisTimelineResponse)
async def get_server_axis_timeline(server_id: int, db: Session = Depends(get_session)):
    # Query all axis scores for the given server_id
    scores = db.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).all()

    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for the given server_id")

    # Organize scores by axis
    axes = {
        "overall_risk": [],
        "auth_strength": [],
        "capability_breadth": [],
        "data_sensitivity": [],
        "network_egress": [],
        "maintainer_trust": [],
        "exploit_surface": []
    }

    for score in scores:
        axis_name = score.axis_name
        if axis_name in axes:
            axes[axis_name].append({
                "scored_at": score.scored_at,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "label": score.label
            })

    # Convert to the expected response format
    response = {
        "server_id": server_id,
        "axes": {
            "overall_risk": axes["overall_risk"],
            "auth_strength": axes["auth_strength"],
            "capability_breadth": axes["capability_breadth"],
            "data_sensitivity": axes["data_sensitivity"],
            "network_egress": axes["network_egress"],
            "maintainer_trust": axes["maintainer_trust"],
            "exploit_surface": axes["exploit_surface"]
        }
    }

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    test_session = TestSessionLocal()
    test_data = [
        McpLlmAxisScores(
            server_id=1,
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 1),
            p_top=0.9,
            p_critical=0.1,
            label="High"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 2),
            p_top=0.8,
            p_critical=0.2,
            label="Medium"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="auth_strength",
            scored_at=datetime(2023, 1, 1),
            p_top=0.7,
            p_critical=0.3,
            label="Medium"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="auth_strength",
            scored_at=datetime(2023, 1, 2),
            p_top=0.6,
            p_critical=0.4,
            label="Low"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="capability_breadth",
            scored_at=datetime(2023, 1, 1),
            p_top=0.5,
            p_critical=0.5,
            label="Low"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="capability_breadth",
            scored_at=datetime(2023, 1, 2),
            p_top=0.4,
            p_critical=0.6,
            label="Very Low"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="data_sensitivity",
            scored_at=datetime(2023, 1, 1),
            p_top=0.3,
            p_critical=0.7,
            label="Very Low"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="data_sensitivity",
            scored_at=datetime(2023, 1, 2),
            p_top=0.2,
            p_critical=0.8,
            label="Critical"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="network_egress",
            scored_at=datetime(2023, 1, 1),
            p_top=0.1,
            p_critical=0.9,
            label="Critical"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="network_egress",
            scored_at=datetime(2023, 1, 2),
            p_top=0.0,
            p_critical=1.0,
            label="Critical"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="maintainer_trust",
            scored_at=datetime(2023, 1, 1),
            p_top=0.9,
            p_critical=0.1,
            label="High"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="maintainer_trust",
            scored_at=datetime(2023, 1, 2),
            p_top=0.8,
            p_critical=0.2,
            label="Medium"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="exploit_surface",
            scored_at=datetime(2023, 1, 1),
            p_top=0.7,
            p_critical=0.3,
            label="Medium"
        ),
        McpLlmAxisScores(
            server_id=1,
            axis_name="exploit_surface",
            scored_at=datetime(2023, 1, 2),
            p_top=0.6,
            p_critical=0.4,
            label="Low"
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    client = TestClient(app)
    response = client.get("/servers/1/axis-timeline")
    assert response.status_code == 200
    data = response.json()

    # Check that each axis has at least 2 time points
    for axis in data["axes"]:
        assert len(data["axes"][axis]) >= 2

    print("PASS")