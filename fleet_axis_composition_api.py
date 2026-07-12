from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class AxisComposition(BaseModel):
    axis_name: str
    label_index_distribution: Dict[int, int]
    mean_p_top: float
    median_p_top: float
    escalated_count: int
    p_top_histogram: Dict[str, int]

class AxisCompositionResponse(BaseModel):
    axes: List[AxisComposition]

def get_axis_composition(db: Session = Depends(get_session)) -> AxisCompositionResponse:
    axes = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
            "network_egress", "maintainer_trust", "exploit_surface"]

    result = []
    for axis in axes:
        # Get label_index distribution
        label_dist = db.query(
            McpLlmAxisScores.label_index,
            func.count(McpLlmAxisScores.label_index).label("count")
        ).filter(
            McpLlmAxisScores.axis_name == axis
        ).group_by(
            McpLlmAxisScores.label_index
        ).all()
        label_index_distribution = {item[0]: item[1] for item in label_dist}

        # Get mean and median p_top
        p_top_stats = db.query(
            func.avg(McpLlmAxisScores.p_top).label("mean"),
            func.percentile_cont(0.5).within_group(McpLlmAxisScores.p_top).label("median")
        ).filter(
            McpLlmAxisScores.axis_name == axis
        ).first()
        mean_p_top = p_top_stats.mean if p_top_stats.mean is not None else 0.0
        median_p_top = p_top_stats.median if p_top_stats.median is not None else 0.0

        # Get escalated count
        escalated_count = db.query(
            func.count(McpLlmAxisScores.server_id)
        ).filter(
            McpLlmAxisScores.axis_name == axis,
            McpLlmAxisScores.escalated == True
        ).scalar() or 0

        # Get p_top histogram
        p_top_histogram = {
            "0-25": 0,
            "25-50": 0,
            "50-75": 0,
            "75-100": 0
        }
        counts = db.query(
            func.count(McpLlmAxisScores.p_top).label("count"),
            func.case(
                (McpLlmAxisScores.p_top < 25, "0-25"),
                (McpLlmAxisScores.p_top < 50, "25-50"),
                (McpLlmAxisScores.p_top < 75, "50-75"),
                else_="75-100"
            ).label("bin")
        ).filter(
            McpLlmAxisScores.axis_name == axis
        ).group_by(
            "bin"
        ).all()
        for count, bin in counts:
            p_top_histogram[bin] = count

        result.append(AxisComposition(
            axis_name=axis,
            label_index_distribution=label_index_distribution,
            mean_p_top=mean_p_top,
            median_p_top=median_p_top,
            escalated_count=escalated_count,
            p_top_histogram=p_top_histogram
        ))

    return AxisCompositionResponse(axes=result)

@router.get("/fleet/axes/composition", response_model=AxisCompositionResponse)
async def fleet_axes_composition(db: Session = Depends(get_session)):
    return get_axis_composition(db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a temporary in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create a test client
    client = TestClient(app)

    # Add some test data
    test_session = TestSession()
    test_data = [
        McpLlmAxisScores(
            server_id=1,
            axis_name="overall_risk",
            label_index=1,
            p_top=10.0,
            escalated=False
        ),
        McpLlmAxisScores(
            server_id=2,
            axis_name="overall_risk",
            label_index=2,
            p_top=30.0,
            escalated=True
        ),
        McpLlmAxisScores(
            server_id=3,
            axis_name="auth_strength",
            label_index=1,
            p_top=20.0,
            escalated=False
        ),
        McpLlmAxisScores(
            server_id=4,
            axis_name="auth_strength",
            label_index=2,
            p_top=40.0,
            escalated=True
        ),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    response = client.get("/fleet/axes/composition")
    assert response.status_code == 200
    data = response.json()

    assert len(data["axes"]) == 7
    for axis in data["axes"]:
        assert "label_index_distribution" in axis
        assert "escalated_count" in axis
        assert "p_top_histogram" in axis

    print("PASS")