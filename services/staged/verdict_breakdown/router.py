from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

from .logic import get_verdict_breakdown

router = APIRouter(prefix="/api")

class AxisBreakdown(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]
    escalated: bool
    decision_rule_version: str
    scored_at: str

class ServerMetadata(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    confidence: float
    last_assessed: str

class VerdictBreakdownResponse(BaseModel):
    server_metadata: ServerMetadata
    axes: List[AxisBreakdown]

@router.get("/verdict/{server_id}/breakdown", response_model=VerdictBreakdownResponse)
async def get_verdict_breakdown_route(
    server_id: str,
    session: Session = Depends(get_session)
):
    breakdown = get_verdict_breakdown(server_id, session)
    if not breakdown:
        raise HTTPException(status_code=404, detail="Server not found")
    return breakdown

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from app.db import get_session as original_get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[original_get_session] = get_test_session

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    with TestSession() as session:
        # Add 3 test servers
        servers = [
            McpServerRegistry(
                server_id=f"server_{i}",
                name=f"Test Server {i}",
                verdict=f"verdict_{i}",
                risk_tier=f"tier_{i}",
                confidence=0.9,
                last_assessed="2023-01-01"
            ) for i in range(1, 4)
        ]
        session.add_all(servers)
        session.commit()

        # Add axis scores for each server
        axes = [
            "overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"
        ]

        for server in servers:
            for axis in axes:
                score = McpLlmAxisScore(
                    server_id=server.server_id,
                    axis_name=axis,
                    label=f"label_{axis}",
                    p_top=0.1,
                    p_critical=0.2,
                    p_danger=0.3,
                    probs=[0.1, 0.2, 0.3, 0.4],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at="2023-01-01"
                )
                session.add(score)
        session.commit()

    # Test the endpoint
    client = TestClient(app)

    # Test each server
    for i in range(1, 4):
        response = client.get(f"/verdict/server_{i}/breakdown")
        assert response.status_code == 200
        data = response.json()

        # Check server metadata
        assert "server_metadata" in data
        metadata = data["server_metadata"]
        assert metadata["server_id"] == f"server_{i}"
        assert metadata["name"] == f"Test Server {i}"
        assert metadata["verdict"] == f"verdict_{i}"
        assert metadata["risk_tier"] == f"tier_{i}"
        assert metadata["confidence"] == 0.9
        assert metadata["last_assessed"] == "2023-01-01"

        # Check axes
        assert "axes" in data
        axes = data["axes"]
        assert len(axes) == 7
        axis_names = [axis["axis_name"] for axis in axes]
        expected_axes = [
            "overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"
        ]
        assert sorted(axis_names) == sorted(expected_axes)

    print("PASS")