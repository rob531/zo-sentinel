from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from datetime import datetime

router = APIRouter()

def get_verdict_summary(server_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    # Fetch server registry data
    server_registry = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server_registry:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    # Prepare axes data
    axes = {}
    for score in axis_scores:
        axes[score.axis_name] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger
        }

    # Additive live-probe EXPOSURE signal (OPEN_EXEC/OPEN_BENIGN/OPEN/GATED/UNREACHABLE).
    # Flag-gated + fail-open in resolve_unauth_access; the import is lazy + wrapped so a
    # resolver/import problem can NEVER orphan or 500 this live public endpoint (it just
    # yields null). Reads the ISOLATED mcp_unauth_access table, never mcp_llm_axis_scores.
    unauth_access = None
    try:
        from unauth_access import resolve_unauth_access
        unauth_access = resolve_unauth_access(session, server_id)
    except Exception:
        unauth_access = None

    # Prepare response
    response = {
        "overall_risk": server_registry.confidence,
        "risk_tier": server_registry.risk_tier,
        "axes": axes,
        "unauth_access": unauth_access,
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
    from app.models import McpServerRegistry, McpLlmAxisScore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup in-memory test database (StaticPool + check_same_thread so TestClient's request
    # thread shares the one in-memory connection)
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                poolclass=StaticPool)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)

    # Create test app + override dependency for testing (test_app must exist first)
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    # Insert test data
    test_server_id = "test_server_123"
    test_server = McpServerRegistry(
        server_id=test_server_id,
        verdict="malicious",
        risk_tier="high",
        confidence=0.95
    )
    test_session.add(test_server)

    test_axes = [
        McpLlmAxisScore(
            id=1,
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
        McpLlmAxisScore(
            id=2,
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
    # Additive exposure field is always present; None here (flag off + no isolated table in test)
    assert "unauth_access" in data, "unauth_access field must be present"
    assert data["unauth_access"] is None, "flag-off/no-table must fail open to null, never 500"

    print("PASS verdict_summary_api")