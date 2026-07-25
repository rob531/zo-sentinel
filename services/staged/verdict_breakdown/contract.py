from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI()

class AxisBreakdown(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]
    escalated: bool
    decision_rule_version: str
    scored_at: datetime

class ServerMetadata(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    confidence: float
    last_assessed: datetime

class VerdictBreakdownResponse(BaseModel):
    server_metadata: ServerMetadata
    axes: List[AxisBreakdown]

def get_axes_for_server(db: Session, server_id: str) -> List[AxisBreakdown]:
    query = db.query(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.p_danger,
        McpLlmAxisScore.probs,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.scored_at
    ).join(
        McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpServerRegistry.server_id == server_id
    ).all()

    return [
        AxisBreakdown(
            axis_name=axis.axis_name,
            label=axis.label,
            p_top=axis.p_top,
            p_critical=axis.p_critical,
            p_danger=axis.p_danger,
            probs=axis.probs,
            escalated=axis.escalated,
            decision_rule_version=axis.decision_rule_version,
            scored_at=axis.scored_at
        )
        for axis in query
    ]

def get_server_metadata(db: Session, server_id: str) -> Optional[ServerMetadata]:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        return None

    return ServerMetadata(
        server_id=server.server_id,
        name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        confidence=server.confidence,
        last_assessed=server.last_assessed
    )

@app.get("/api/verdict/{server_id}/breakdown", response_model=VerdictBreakdownResponse)
async def get_verdict_breakdown(server_id: str, db: Session = Depends(get_session)):
    server_metadata = get_server_metadata(db, server_id)
    if not server_metadata:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = get_axes_for_server(db, server_id)
    if not axes:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    return VerdictBreakdownResponse(
        server_metadata=server_metadata,
        axes=axes
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server_ids = ["server1", "server2", "server3"]
    test_axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust",
        "exploit_surface"
    ]

    for server_id in test_server_ids:
        test_session.add(McpServerRegistry(
            server_id=server_id,
            name=f"Test Server {server_id}",
            verdict="safe",
            risk_tier="low",
            confidence=0.9,
            last_assessed=datetime.now()
        ))

        for axis in test_axes:
            test_session.add(McpLlmAxisScore(
                server_id=server_id,
                axis_name=axis,
                label=f"Label for {axis}",
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.3,
                probs=[0.1, 0.2, 0.3, 0.4],
                escalated=False,
                decision_rule_version="1.0",
                scored_at=datetime.now()
            ))

    test_session.commit()

    # Run tests
    client = TestClient(app)

    for server_id in test_server_ids:
        response = client.get(f"/api/verdict/{server_id}/breakdown")
        assert response.status_code == 200
        data = response.json()

        assert "server_metadata" in data
        metadata = data["server_metadata"]
        assert metadata["server_id"] == server_id
        assert "name" in metadata
        assert "verdict" in metadata
        assert "risk_tier" in metadata
        assert "confidence" in metadata
        assert "last_assessed" in metadata

        assert "axes" in data
        axes = data["axes"]
        assert len(axes) == 7
        axis_names = [axis["axis_name"] for axis in axes]
        assert set(axis_names) == set(test_axes)

    print("PASS")