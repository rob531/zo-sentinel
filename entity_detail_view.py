from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter(prefix="/entity", tags=["entity"])

class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    escalated: bool
    scored_at: datetime

class EntityDetail(BaseModel):
    server_id: str
    name: str
    registry_source: str
    trust_score: float
    verdict: str
    risk_tier: str
    last_assessed: datetime
    scan_count: int
    axes: List[AxisScore]
    meta: Dict[str, Optional[str]]

@router.get("/{server_id}", response_model=EntityDetail)
async def get_entity_detail(server_id: str, db: Session = Depends(get_session)) -> EntityDetail:
    # Fetch server registry data
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch axis scores
    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    # Prepare response
    axes = [
        AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            label_index=score.label_index,
            p_top=score.p_top,
            p_critical=score.p_critical,
            escalated=score.escalated,
            scored_at=score.scored_at
        )
        for score in axis_scores
    ]

    return EntityDetail(
        server_id=server.server_id,
        name=server.name,
        registry_source=server.registry_source,
        trust_score=server.trust_score,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        last_assessed=server.last_assessed,
        scan_count=server.scan_count,
        axes=axes,
        meta={
            "source": "mcp_server_registry",
            "scores_source": "mcp_llm_axis_scores",
            "timestamp": datetime.utcnow().isoformat()
        }
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-server-123",
        name="Test Server",
        registry_source="test_source",
        trust_score=0.9,
        verdict="safe",
        risk_tier="low",
        last_assessed=datetime.utcnow(),
        scan_count=5
    )
    test_session.add(test_server)

    test_axes = [
        MCPLLMAxisScores(
            server_id="test-server-123",
            axis_name="axis1",
            label="label1",
            label_index=1,
            p_top=0.8,
            p_critical=0.2,
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id="test-server-123",
            axis_name="axis2",
            label="label2",
            label_index=2,
            p_top=0.7,
            p_critical=0.3,
            escalated=True,
            scored_at=datetime.utcnow()
        )
    ]
    test_session.add_all(test_axes)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/entity/test-server-123")
    assert response.status_code == 200
    data = response.json()

    # Verify all required fields are present
    assert "server_id" in data
    assert "name" in data
    assert "registry_source" in data
    assert "trust_score" in data
    assert "verdict" in data
    assert "risk_tier" in data
    assert "last_assessed" in data
    assert "scan_count" in data
    assert "axes" in data
    assert len(data["axes"]) == 2  # Should have 2 test axes
    assert "meta" in data

    print("PASS")