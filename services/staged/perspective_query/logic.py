from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Optional
from uuid import UUID
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, PerspectiveSnapshot
from sqlalchemy.orm import Session
from pydantic import BaseModel

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class PerspectiveQueryResponse(BaseModel):
    perspective_id: str
    server_id: str
    axes: Dict[str, AxisScore]
    overall_risk: float
    risk_tier: str

def calculate_risk_tier(overall_risk: float) -> str:
    if overall_risk >= 0.8:
        return "High"
    elif overall_risk >= 0.5:
        return "Medium"
    else:
        return "Low"

def get_perspective_query(
    perspective_id: UUID,
    server_id: str,
    session: Session = Depends(get_session)
) -> Optional[PerspectiveQueryResponse]:
    # Query the database to get the required data
    query = session.query(
        PerspectiveSnapshot.perspective_id,
        McpServerRegistry.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.p_danger,
        PerspectiveSnapshot.overall_risk
    ).join(
        McpServerRegistry, PerspectiveSnapshot.server_id == McpServerRegistry.server_id
    ).join(
        McpLlmAxisScore,
        (PerspectiveSnapshot.perspective_id == McpLlmAxisScore.perspective_id) &
        (PerspectiveSnapshot.server_id == McpLlmAxisScore.server_id)
    ).filter(
        PerspectiveSnapshot.perspective_id == perspective_id,
        McpServerRegistry.server_id == server_id
    ).all()

    if not query:
        return None

    # Process the query results
    perspective_id_str = str(query[0].perspective_id)
    server_id_str = query[0].server_id
    axes = {}
    overall_risk = query[0].overall_risk

    for row in query:
        axes[row.axis_name] = {
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger
        }

    risk_tier = calculate_risk_tier(overall_risk)

    return PerspectiveQueryResponse(
        perspective_id=perspective_id_str,
        server_id=server_id_str,
        axes=axes,
        overall_risk=overall_risk,
        risk_tier=risk_tier
    )

@router.get("/api/perspective/query", response_model=PerspectiveQueryResponse)
async def perspective_query(
    perspective_id: str,
    server_id: str,
    session: Session = Depends(get_session)
):
    try:
        perspective_id_uuid = UUID(perspective_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid perspective_id format")

    result = get_perspective_query(perspective_id_uuid, server_id, session)

    if result is None:
        raise HTTPException(status_code=404, detail="Perspective or server not found")

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_session = SessionLocal()

    # Add test data to the database
    test_server = McpServerRegistry(server_id="test_server")
    test_session.add(test_server)
    test_session.commit()

    test_perspective = PerspectiveSnapshot(
        perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
        server_id="test_server",
        overall_risk=0.6
    )
    test_session.add(test_perspective)
    test_session.commit()

    test_axes = [
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis1",
            label="Label 1",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.3
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis2",
            label="Label 2",
            p_top=0.2,
            p_critical=0.3,
            p_danger=0.4
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis3",
            label="Label 3",
            p_top=0.3,
            p_critical=0.4,
            p_danger=0.5
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis4",
            label="Label 4",
            p_top=0.4,
            p_critical=0.5,
            p_danger=0.6
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis5",
            label="Label 5",
            p_top=0.5,
            p_critical=0.6,
            p_danger=0.7
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis6",
            label="Label 6",
            p_top=0.6,
            p_critical=0.7,
            p_danger=0.8
        ),
        McpLlmAxisScore(
            perspective_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
            server_id="test_server",
            axis_name="axis7",
            label="Label 7",
            p_top=0.7,
            p_critical=0.8,
            p_danger=0.9
        )
    ]
    test_session.add_all(test_axes)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/perspective/query?perspective_id=123e4567-e89b-12d3-a456-426614174000&server_id=test_server")

    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert data["axes"]["axis1"]["p_top"] == 0.1
    assert data["axes"]["axis2"]["p_top"] == 0.2
    assert data["axes"]["axis3"]["p_top"] == 0.3
    assert data["axes"]["axis4"]["p_top"] == 0.4
    assert data["axes"]["axis5"]["p_top"] == 0.5
    assert data["axes"]["axis6"]["p_top"] == 0.6
    assert data["axes"]["axis7"]["p_top"] == 0.7

    print("PASS")