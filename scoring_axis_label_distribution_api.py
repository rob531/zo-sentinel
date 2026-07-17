from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores
from pydantic import BaseModel

router = APIRouter()

class AxisLabelDistribution(BaseModel):
    labels: Dict[str, int]
    p_top_avg: float

class AxisDistributionResponse(BaseModel):
    axes: Dict[str, AxisLabelDistribution]

@router.get("/scoring/axis-label-distribution", response_model=AxisDistributionResponse)
def get_axis_label_distribution(db: Session = Depends(get_session)):
    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]

    result = {axis: {"labels": {}, "p_top_avg": 0.0} for axis in axes}

    for axis in axes:
        # Query the database for label distribution and p_top_avg
        query = db.query(
            McpLlmAxisScores.label,
            McpLlmAxisScores.p_top,
            McpLlmAxisScores.server_id
        ).filter(McpLlmAxisScores.axis == axis).all()

        # Count labels and calculate average p_top
        label_counts = {}
        p_top_sum = 0.0
        count = 0

        for row in query:
            label = row.label
            p_top = row.p_top

            label_counts[label] = label_counts.get(label, 0) + 1
            p_top_sum += p_top
            count += 1

        if count > 0:
            result[axis]["labels"] = label_counts
            result[axis]["p_top_avg"] = p_top_sum / count

    return {"axes": result}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import McpLlmAxisScores
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.dependency_overrides import dependency_overrides

    # Create a test database in memory
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    McpLlmAxisScores.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    async def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = override_get_session

    # Create test data
    test_data = [
        McpLlmAxisScores(
            server_id=1,
            axis="overall_risk",
            label="low",
            p_top=0.9
        ),
        McpLlmAxisScores(
            server_id=1,
            axis="auth_strength",
            label="medium",
            p_top=0.8
        ),
        McpLlmAxisScores(
            server_id=2,
            axis="overall_risk",
            label="medium",
            p_top=0.7
        ),
        McpLlmAxisScores(
            server_id=2,
            axis="auth_strength",
            label="high",
            p_top=0.6
        ),
        McpLlmAxisScores(
            server_id=3,
            axis="overall_risk",
            label="high",
            p_top=0.5
        ),
        McpLlmAxisScores(
            server_id=3,
            axis="auth_strength",
            label="low",
            p_top=0.4
        ),
    ]

    # Add test data to the session
    session = SessionLocal()
    session.add_all(test_data)
    session.commit()
    session.close()

    # Create a test client
    from main import app
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/scoring/axis-label-distribution")
    assert response.status_code == 200
    data = response.json()

    # Verify the response structure
    assert isinstance(data, dict)
    assert "axes" in data
    assert isinstance(data["axes"], dict)

    # Verify all axes are present
    expected_axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]
    for axis in expected_axes:
        assert axis in data["axes"]

    # Verify the label counts and p_top_avg for the axes with test data
    assert data["axes"]["overall_risk"]["labels"] == {"low": 1, "medium": 1, "high": 1}
    assert data["axes"]["overall_risk"]["p_top_avg"] == 0.7

    assert data["axes"]["auth_strength"]["labels"] == {"medium": 1, "high": 1, "low": 1}
    assert data["axes"]["auth_strength"]["p_top_avg"] == 0.6

    # Verify the other axes have empty labels and 0 p_top_avg
    for axis in ["capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
        assert data["axes"][axis]["labels"] == {}
        assert data["axes"][axis]["p_top_avg"] == 0.0

    print("PASS")