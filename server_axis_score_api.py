from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScore
from typing import Dict, Any
from datetime import datetime

router = APIRouter()

def get_server_axis_score(server_id: str, axis_name: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    valid_axes = {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    }

    if axis_name not in valid_axes:
        raise HTTPException(status_code=400, detail="Invalid axis name")

    score = session.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id,
        MCPLLMAxisScore.axis_name == axis_name
    ).first()

    if not score:
        raise HTTPException(status_code=404, detail="Score not found")

    return {
        "axis_name": score.axis_name,
        "label": score.label,
        "probs": score.probs,
        "p_top": score.p_top,
        "p_critical": score.p_critical,
        "p_danger": score.p_danger,
        "escalated": score.escalated,
        "escalated_to": score.escalated_to,
        "decision_rule_version": score.decision_rule_version,
        "model_version": score.model_version,
        "scored_at": score.scored_at.isoformat() if score.scored_at else None
    }

@router.get("/servers/{server_id}/axis/{axis_name}", response_model=Dict[str, Any])
async def read_server_axis_score(server_id: str, axis_name: str, session: Session = Depends(get_session)):
    return get_server_axis_score(server_id, axis_name, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import Base, engine
    from app.models import MCPLLMAxisScore
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Create test data
    test_score = MCPLLMAxisScore(
        server_id="test_server",
        axis_name="overall_risk",
        label="high",
        probs={"low": 0.1, "medium": 0.2, "high": 0.7},
        p_top=0.7,
        p_critical=0.5,
        p_danger=0.3,
        escalated=False,
        escalated_to=None,
        decision_rule_version="1.0",
        model_version="2.0",
        scored_at=datetime.now()
    )

    test_session.add(test_score)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test_server/axis/overall_risk")
    assert response.status_code == 200
    assert "axis_name" in response.json()
    assert "label" in response.json()
    assert "probs" in response.json()
    assert "p_top" in response.json()
    assert "p_critical" in response.json()
    assert "p_danger" in response.json()
    assert "escalated" in response.json()
    assert "escalated_to" in response.json()
    assert "decision_rule_version" in response.json()
    assert "model_version" in response.json()
    assert "scored_at" in response.json()

    print("PASS")