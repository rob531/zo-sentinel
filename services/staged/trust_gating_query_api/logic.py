from typing import Optional
from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry, Base

try:
    from app.trust_gating_override import trust_gate
except ImportError:
    trust_gate = None


class TrustGateResponse(BaseModel):
    server_id: str
    url: str
    verdict: Optional[str] = None
    trusted: bool
    published_overall_risk: Optional[str] = None
    gating_applied: bool
    reason: Optional[str] = None


def get_server_gate(
    server_id: str,
    url: str,
    session: Session
) -> TrustGateResponse:
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        return TrustGateResponse(
            server_id=server_id,
            url=url,
            trusted=False,
            gating_applied=True,
            reason="Server not found in registry"
        )

    name = server.name
    axis_dict = {
        "risk_tier": server.risk_tier,
        "trust_score": server.trust_score,
        "confidence": server.confidence,
        "verdict": server.verdict,
        "verdict_reasoning": server.verdict_reasoning,
    }

    if trust_gate:
        result = trust_gate(url, name, axis_dict)
        trusted = result.get("trusted", False)
        published_overall_risk = result.get("published_overall_risk", server.risk_tier)
        gating_applied = result.get("gating_applied", True)
        reason = result.get("reason", server.verdict_reasoning)
    else:
        trusted = server.verdict == "trusted" if server.verdict else False
        published_overall_risk = server.risk_tier
        gating_applied = True
        reason = server.verdict_reasoning

    return TrustGateResponse(
        server_id=server_id,
        url=url,
        verdict=server.verdict,
        trusted=trusted,
        published_overall_risk=published_overall_risk,
        gating_applied=gating_applied,
        reason=reason
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app = FastAPI()

    def mock_trust_gate(url: str, name: str, axis_dict: dict):
        risk_tier = axis_dict.get("risk_tier", "high")
        trust_score = axis_dict.get("trust_score", 0)
        if risk_tier == "low" and trust_score and trust_score > 0.7:
            return {
                "trusted": True,
                "published_overall_risk": "low",
                "gating_applied": True,
                "reason": "Meets trust criteria"
            }
        return {
            "trusted": False,
            "published_overall_risk": risk_tier or "high",
            "gating_applied": True,
            "reason": "Does not meet trust criteria"
        }

    import services.staged.trust_gating_query_api.logic as logic_module
    logic_module.trust_gate = mock_trust_gate

    @app.get("/api/trust/gate")
    def endpoint(server_id: str = Query(...), url: str = Query(...)):
        session = TestSession()
        try:
            return logic_module.get_server_gate(server_id, url, session)
        finally:
            session.close()

    session = TestSession()
    session.add(McpServerRegistry(
        server_id="srv_trusted_001",
        name="Trusted Server",
        url="https://trusted.example.com",
        risk_tier="low",
        trust_score=0.85,
        confidence=0.9,
        verdict="trusted",
        verdict_reasoning="Meets criteria"
    ))
    session.add(McpServerRegistry(
        server_id="srv_untrusted_001",
        name="Untrusted Server",
        url="https://untrusted.example.com",
        risk_tier="high",
        trust_score=0.3,
        confidence=0.5,
        verdict="untrusted",
        verdict_reasoning="Fails criteria"
    ))
    session.commit()
    session.close()

    client = TestClient(app)

    resp = client.get("/api/trust/gate?server_id=srv_trusted_001&url=https://trusted.example.com")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["trusted"], bool)
    assert data["published_overall_risk"] == "low"

    resp = client.get("/api/trust/gate?server_id=srv_untrusted_001&url=https://untrusted.example.com")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["trusted"], bool)
    assert data["published_overall_risk"] == "high"

    print("PASS")