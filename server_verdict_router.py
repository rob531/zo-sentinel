"""Server verdict router -- endpoints for MCP server risk assessment results.

Backed by the app.db session and the McpServerRegistry model.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from app.rbac import require_role
from app.security import Principal, get_principal

router = APIRouter(prefix="/servers", tags=["verdicts"])

class ServerVerdictResponse(BaseModel):
    """Server verdict response model"""
    server_id: str
    name: Optional[str]
    url: Optional[str]
    verdict: Optional[str]
    verdict_reasoning: Optional[str]
    confidence: Optional[float]
    risk_tier: Optional[str]
    last_assessed: Optional[str]
    trust_score: Optional[float]

class UpdateVerdictRequest(BaseModel):
    """Request model for updating a server's verdict"""
    verdict: str
    verdict_reasoning: str
    confidence: float
    risk_tier: str
    trust_score: Optional[float]

@router.get("/{server_id}/verdict", response_model=ServerVerdictResponse)
def get_server_verdict(
    server_id: str,
    sess: Session = Depends(get_session),
    principal: Principal = Depends(get_principal)
):
    """Get verdict details for a specific server"""
    server = sess.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    return ServerVerdictResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning,
        confidence=server.confidence,
        risk_tier=server.risk_tier,
        last_assessed=str(server.last_assessed) if server.last_assessed else None,
        trust_score=server.trust_score
    )

@router.get("/verdicts", response_model=list[ServerVerdictResponse])
def list_server_verdicts(
    limit: int = 100,
    offset: int = 0,
    sess: Session = Depends(get_session),
    principal: Principal = Depends(get_principal)
):
    """List server verdicts with pagination"""
    servers = sess.execute(
        select(McpServerRegistry)
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return [
        ServerVerdictResponse(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            verdict=server.verdict,
            verdict_reasoning=server.verdict_reasoning,
            confidence=server.confidence,
            risk_tier=server.risk_tier,
            last_assessed=str(server.last_assessed) if server.last_assessed else None,
            trust_score=server.trust_score
        )
        for server in servers
    ]

@router.put("/{server_id}/verdict", response_model=ServerVerdictResponse)
def update_server_verdict(
    server_id: str,
    request: UpdateVerdictRequest,
    sess: Session = Depends(get_session),
    principal: Principal = Depends(require_role("admin"))
):
    """Update a server's verdict (admin-only)"""
    server = sess.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    server.verdict = request.verdict
    server.verdict_reasoning = request.verdict_reasoning
    server.confidence = request.confidence
    server.risk_tier = request.risk_tier
    if request.trust_score is not None:
        server.trust_score = request.trust_score

    sess.commit()

    return ServerVerdictResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning,
        confidence=server.confidence,
        risk_tier=server.risk_tier,
        last_assessed=str(server.last_assessed) if server.last_assessed else None,
        trust_score=server.trust_score
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy.pool import StaticPool
    from app.db import get_session, engine, Base

    # Test setup
    app.dependency_overrides[get_session] = lambda: get_session().get_bind().connect()

    # Create test database
    Base.metadata.create_all(engine)

    # Test data
    test_server = McpServerRegistry(
        server_id="test-server-1",
        name="Test Server",
        url="https://test.example.com",
        verdict="SAFE",
        verdict_reasoning="Test reasoning",
        confidence=0.95,
        risk_tier="LOW",
        trust_score=0.8,
        last_assessed=None
    )

    # Insert test data
    with engine.connect() as conn:
        conn.execute(test_server.__table__.insert(), [test_server.__dict__])
        conn.commit()

    client = TestClient(app)

    # Test get single verdict
    resp = client.get("/servers/test-server-1/verdict")
    assert resp.status_code == 200
    assert resp.json()["server_id"] == "test-server-1"
    assert resp.json()["verdict"] == "SAFE"

    # Test list verdicts
    resp = client.get("/servers/verdicts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Test update verdict (requires admin role)
    with patch("app.security.get_principal") as mock_principal:
        mock_principal.return_value = Principal(
            user_id="test-admin",
            org_id="test-org",
            role="admin",
            email="admin@example.com"
        )
        update_data = {
            "verdict": "HIGH",
            "verdict_reasoning": "Updated reasoning",
            "confidence": 0.9,
            "risk_tier": "HIGH",
            "trust_score": 0.7
        }
        resp = client.put("/servers/test-server-1/verdict", json=update_data)
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "HIGH"

    # Test unauthorized update
    with patch("app.security.get_principal") as mock_principal:
        mock_principal.return_value = Principal(
            user_id="test-user",
            org_id="test-org",
            role="member",
            email="user@example.com"
        )
        resp = client.put("/servers/test-server-1/verdict", json=update_data)
        assert resp.status_code == 403

    print("PASS")
