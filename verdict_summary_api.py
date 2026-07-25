from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from datetime import datetime

router = APIRouter()

def get_verdict_summary(server_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    # Fetch server registry data
    server_registry = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server_registry:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch axis scores
    axis_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    # Prepare axes data
    axes = {}
    for score in axis_scores:
        axes[score.axis_name] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger
        }

    # Prepare response
    response = {
        "overall_risk": server_registry.confidence,
        "risk_tier": server_registry.risk_tier,
        "axes": axes,
        "meta": {
            "timestamp": datetime.utcnow().isoformat(),
            "source_version": "1.0"
        }
    }

    return response

router.get("/verdict_summary/{server_id}")(get_verdict_summary)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    MCPServerRegistry.__table__.create(test_engine)
    MCPLLMAxisScores.__table__.create(test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Insert test data
    test_server_id = "test_server_123"
    test_server = MCPServerRegistry(
        server_id=test_server_id,
        verdict="malicious",
        risk_tier="high",
        confidence=0.95
    )
    test_session.add(test_server)

    test_axes = [
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis1",
            label="Label 1",
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.1,
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis2",
            label="Label 2",
            p_top=0.7,
            p_critical=0.3,
            p_danger=0.2,
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.utcnow()
        ),
        # Add more axes as needed for testing
    ]
    test_session.add_all(test_axes)
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get(f"/verdict_summary/{test_server_id}")
    assert response.status_code == 200
    data = response.json()

    # Verify response contains all axes and correct risk_tier
    assert len(data["axes"]) >= 2  # At least 2 axes in test data
    assert data["risk_tier"] == "high"

    print("PASS verdict_summary_api")