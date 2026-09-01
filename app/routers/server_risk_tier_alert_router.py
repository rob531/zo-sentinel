# deps: fastapi, sqlalchemy, pydantic
import sys as _sys
_sys.path.insert(0, "/home/workspace/zo_sentinel")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime

from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter()


class RiskTierAlertResponse(BaseModel):
    server_id: str
    risk_tier: str
    verdict: str
    confidence: float
    last_assessed: str


@router.get("/alerts/{server_id}", response_model=RiskTierAlertResponse)
async def read_risk_tier_alert(server_id: str, session: Session = Depends(get_session)):
    """Return risk-tier alert data for a server at /alerts/{server_id}."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return RiskTierAlertResponse(
        server_id=server.server_id,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        last_assessed=server.last_assessed.isoformat(),
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session, Base, engine
    from sqlalchemy.orm import sessionmaker

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    test_server = McpServerRegistry(
        server_id="srv-123",
        risk_tier="high",
        verdict="malicious",
        confidence=0.95,
        last_assessed=datetime.now(),
    )
    test_session.add(test_server)
    test_session.commit()

    def _override():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    response = client.get("/api/v1/alerts/srv-123")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["server_id"] == "srv-123", f"server_id mismatch: {data}"
    assert data["risk_tier"] == "high", f"risk_tier mismatch: {data}"
    assert data["verdict"] == "malicious", f"verdict mismatch: {data}"
    assert data["confidence"] == 0.95, f"confidence mismatch: {data}"
    assert "last_assessed" in data, f"missing last_assessed: {data}"

    resp_404 = client.get("/api/v1/alerts/nonexistent")
    assert resp_404.status_code == 404, f"Expected 404 for unknown server, got {resp_404.status_code}"

    health_resp = client.get("/health")
    assert health_resp.status_code == 200, f"health endpoint broken: {health_resp.status_code}"

    print("PASS")
