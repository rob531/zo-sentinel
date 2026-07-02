from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class ServerComparison(BaseModel):
    server_id: str
    name: str
    risk_score: float
    last_assessed: str
    axes: Dict[str, AxisScore]

class ServerComparisonResponse(BaseModel):
    servers: List[ServerComparison]
    total: int

@router.get("/server-compare", response_model=ServerComparisonResponse)
async def get_server_comparison(db: Session = Depends(get_session)):
    # Query server registry
    servers = db.query(MCPServerRegistry).all()

    # Prepare response data
    response_data = {
        "servers": [],
        "total": len(servers)
    }

    for server in servers:
        # Query axis scores for each server
        axis_scores = db.query(
            MCPLLMAxisScores.axis,
            MCPLLMAxisScores.label,
            MCPLLMAxisScores.p_top
        ).filter(
            MCPLLMAxisScores.server_id == server.server_id
        ).all()

        # Prepare axes data
        axes_data = {}
        for score in axis_scores:
            axes_data[score.axis] = {
                "label": score.label,
                "p_top": score.p_top
            }

        # Add server to response
        response_data["servers"].append({
            "server_id": server.server_id,
            "name": server.name,
            "risk_score": server.risk_score,
            "last_assessed": server.last_assessed.isoformat() if server.last_assessed else None,
            "axes": axes_data
        })

    return response_data

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Create in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        name="Test Server 1",
        risk_score=0.75,
        last_assessed="2023-01-01T00:00:00"
    )
    test_session.add(test_server)

    test_axis1 = MCPLLMAxisScores(
        server_id="test-server-1",
        axis="axis1",
        label="Test Axis 1",
        p_top=0.85
    )
    test_axis2 = MCPLLMAxisScores(
        server_id="test-server-1",
        axis="axis2",
        label="Test Axis 2",
        p_top=0.90
    )
    test_session.add_all([test_axis1, test_axis2])
    test_session.commit()

    # Test endpoint
    client = TestClient(test_app)
    response = client.get("/server-compare")
    assert response.status_code == 200
    assert response.json() == {
        "servers": [
            {
                "server_id": "test-server-1",
                "name": "Test Server 1",
                "risk_score": 0.75,
                "last_assessed": "2023-01-01T00:00:00",
                "axes": {
                    "axis1": {"label": "Test Axis 1", "p_top": 0.85},
                    "axis2": {"label": "Test Axis 2", "p_top": 0.90}
                }
            }
        ],
        "total": 1
    }

    print("PASS")