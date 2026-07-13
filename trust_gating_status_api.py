from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from trust_gating_override import trust_gate

router = APIRouter()

class TrustGatingStatus(BaseModel):
    server_id: str
    trust_gate: str
    override_source: Optional[str]
    effective_verdict: Optional[str]
    checked_at: str

@router.get("/servers/{server_id}/trust-gating", response_model=TrustGatingStatus)
async def get_trust_gating_status(server_id: str, session=Depends(get_session)):
    server = session.query(MCPServerRegistry).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = session.query(MCPLLMAxisScores).filter_by(server_id=server_id).first()
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Axis scores not found for server")

    axis_labels = {
        "capability": axis_scores.capability_label,
        "alignment": axis_scores.alignment_label,
        "safety": axis_scores.safety_label,
        "privacy": axis_scores.privacy_label,
        "fairness": axis_scores.fairness_label,
        "transparency": axis_scores.transparency_label
    }

    gate_result = trust_gate(server.url, server.name, axis_labels)

    return {
        "server_id": server_id,
        "trust_gate": gate_result["trust_gate"],
        "override_source": gate_result["override_source"],
        "effective_verdict": gate_result["effective_verdict"],
        "checked_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Base

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create a test database
    Base.metadata.create_all(bind=SessionLocal().get_bind())

    # Add test data
    test_session = SessionLocal()
    test_session.add(MCPServerRegistry(
        id="test-server-1",
        name="Trusted Server",
        url="https://trusted.example.com",
        description="Test server with trusted override"
    ))
    test_session.add(MCPLLMAxisScores(
        server_id="test-server-1",
        capability_label="TRUSTED_GENERAL",
        alignment_label="TRUSTED_GENERAL",
        safety_label="TRUSTED_GENERAL",
        privacy_label="TRUSTED_GENERAL",
        fairness_label="TRUSTED_GENERAL",
        transparency_label="TRUSTED_GENERAL"
    ))
    test_session.add(MCPServerRegistry(
        id="test-server-2",
        name="Untrusted Server",
        url="https://untrusted.example.com",
        description="Test server without override"
    ))
    test_session.add(MCPLLMAxisScores(
        server_id="test-server-2",
        capability_label="ENTERPRISE_CONTROLLED",
        alignment_label="ENTERPRISE_CONTROLLED",
        safety_label="ENTERPRISE_CONTROLLED",
        privacy_label="ENTERPRISE_CONTROLLED",
        fairness_label="ENTERPRISE_CONTROLLED",
        transparency_label="ENTERPRISE_CONTROLLED"
    ))
    test_session.commit()

    client = TestClient(app)

    # Test trusted server
    response = client.get("/servers/test-server-1/trust-gating")
    assert response.status_code == 200
    assert response.json()["trust_gate"] == "trusted"
    assert response.json()["override_source"] is not None

    # Test untrusted server
    response = client.get("/servers/test-server-2/trust-gating")
    assert response.status_code == 200
    assert response.json()["trust_gate"] == "untrusted"
    assert response.json()["override_source"] is None

    print("PASS")