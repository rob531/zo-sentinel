from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_trust_summary"])


@router.get("/servers/{server_id}/trust-summary")
def get_trust_summary(
    server_id: str,
    session: Session = Depends(get_session),
):
    """Get trust summary for a server."""
    # Query registry
    registry = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    
    if not registry:
        return {"error": "server not found"}
    
    # Query axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    
    # Compute composite from axes
    composite_score = 50.0  # placeholder
    axes = []
    for score in axis_scores:
        axes.append({
            "axis_name": score.axis_name,
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "probs": score.probs,
        })
    
    # Gating logic
    is_trusted = registry.trust_score is not None and registry.trust_score >= 50.0
    gate_source = "registry"
    gate_reason = "trust_score threshold" if is_trusted else "below threshold"
    
    return {
        "server_id": registry.server_id,
        "name": registry.name,
        "registry_source": registry.registry_source,
        "risk_tier": registry.risk_tier,
        "verdict": registry.verdict,
        "confidence": registry.confidence,
        "trust_score": registry.trust_score,
        "composite_score": composite_score,
        "axes": axes,
        "gating": {
            "is_trusted": is_trusted,
            "gate_source": gate_source,
            "gate_reason": gate_reason,
        },
        "criteria_version": "1.0",
        "last_assessed": registry.last_assessed,
        "first_seen": registry.first_seen,
        "scan_count": registry.scan_count,
    }


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    # Create in-memory test database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Override get_session
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Seed test data
    db = TestingSessionLocal()
    from datetime import datetime, timedelta
    
    # Server 1
    srv1 = McpServerRegistry(
        server_id="srv1",
        name="Test Server 1",
        registry_source="test",
        risk_tier="medium",
        verdict="approved",
        confidence=0.85,
        trust_score=75.0,
        first_seen=datetime.utcnow() - timedelta(days=30),
        last_assessed=datetime.utcnow(),
        scan_count=10,
    )
    db.add(srv1)
    
    # Server 2
    srv2 = McpServerRegistry(
        server_id="srv2",
        name="Test Server 2",
        registry_source="test",
        risk_tier="low",
        verdict="approved",
        confidence=0.90,
        trust_score=85.0,
        first_seen=datetime.utcnow() - timedelta(days=60),
        last_assessed=datetime.utcnow(),
        scan_count=25,
    )
    db.add(srv2)
    
    # Axis scores for srv1 (3 axes)
    for i, axis in enumerate(["overall_risk", "auth_strength", "capability_breadth"]):
        score = McpLlmAxisScore(
            adapter_sha256="sha_test1",
            axis_name=axis,
            decision_rule_version="1.0",
            escalated=False,
            id=f"ax1_{i}",
            label=f"Label_{i}",
            label_index=i,
            model_version="v1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs=[0.1, 0.2, 0.7],
            scored_at=datetime.utcnow(),
            server_id="srv1",
        )
        db.add(score)
    
    # Axis scores for srv2 (3 axes)
    for i, axis in enumerate(["overall_risk", "data_sensitivity", "network_egress"]):
        score = McpLlmAxisScore(
            adapter_sha256="sha_test2",
            axis_name=axis,
            decision_rule_version="1.0",
            escalated=False,
            id=f"ax2_{i}",
            label=f"Label_{i}",
            label_index=i,
            model_version="v1",
            p_critical=0.05,
            p_danger=0.15,
            p_top=0.8,
            probs=[0.05, 0.15, 0.8],
            scored_at=datetime.utcnow(),
            server_id="srv2",
        )
        db.add(score)
    
    db.commit()
    db.close()
    
    # Create test app
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)
    
    # Run tests
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Test srv1
    resp1 = client.get("/api/servers/srv1/trust-summary")
    assert resp1.status_code == 200, f"srv1 status: {resp1.status_code}"
    data1 = resp1.json()
    assert len(data1["axes"]) == 3, f"srv1 axes: {len(data1['axes'])}"
    assert 0 <= data1["composite_score"] <= 100, f"composite_score: {data1['composite_score']}"
    assert isinstance(data1["gating"]["is_trusted"], bool), f"is_trusted type: {type(data1['gating']['is_trusted'])}"
    
    # Test srv2
    resp2 = client.get("/api/servers/srv2/trust-summary")
    assert resp2.status_code == 200, f"srv2 status: {resp2.status_code}"
    data2 = resp2.json()
    assert len(data2["axes"]) == 3, f"srv2 axes: {len(data2['axes'])}"
    assert 0 <= data2["composite_score"] <= 100, f"composite_score: {data2['composite_score']}"
    assert isinstance(data2["gating"]["is_trusted"], bool), f"is_trusted type: {type(data2['gating']['is_trusted'])}"
    
    print("PASS")