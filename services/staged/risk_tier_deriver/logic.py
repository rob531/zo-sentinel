from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

class DerivationResult(BaseModel):
    derived: int
    updated: int
    skipped: int

def calculate_composite_score(axis_scores: List[McpLlmAxisScore]) -> float:
    """Calculate weighted average composite score from axis p_top values."""
    weights = [0.2, 0.15, 0.15, 0.1, 0.1, 0.1, 0.2]  # PRODUCT_SPEC §2 weights
    if len(axis_scores) != 7:
        raise ValueError("Exactly 7 axis scores required")
    return sum(score.p_top * weight for score, weight in zip(axis_scores, weights))

def map_to_risk_tier(composite: float) -> str:
    """Map composite score to risk tier."""
    if composite > 75:
        return "TRUSTED_GENERAL"
    elif composite > 60:
        return "TRUSTED_RESEARCH"
    elif composite > 45:
        return "ENTERPRISE_CONTROLLED"
    elif composite > 30:
        return "CAUTION_LIMITED"
    elif composite > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def derive_risk_tiers(db: Session, server_id: Optional[str] = None) -> DerivationResult:
    """Derive risk tiers for servers with axis scores."""
    query = db.query(McpServerRegistry).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).group_by(McpServerRegistry.server_id)

    if server_id:
        query = query.filter(McpServerRegistry.server_id == server_id)

    servers = query.all()
    derived = 0
    updated = 0
    skipped = 0

    for server in servers:
        # Get all axis scores for this server
        axis_scores = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server.server_id
        ).order_by(McpLlmAxisScore.label_index).all()

        if len(axis_scores) != 7:
            skipped += 1
            continue

        try:
            composite = calculate_composite_score(axis_scores)
            new_tier = map_to_risk_tier(composite)

            if server.risk_tier != new_tier:
                server.risk_tier = new_tier
                db.add(server)
                updated += 1
            else:
                skipped += 1

            derived += 1
        except Exception as e:
            skipped += 1
            continue

    db.commit()
    return DerivationResult(derived=derived, updated=updated, skipped=skipped)

def get_app() -> FastAPI:
    app = FastAPI()

    @app.post("/derive", response_model=DerivationResult)
    async def derive(
        server_id: Optional[str] = None,
        db: Session = Depends(get_session)
    ) -> DerivationResult:
        return derive_risk_tiers(db, server_id)

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app = get_app()
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Create test servers
        servers = [
            McpServerRegistry(server_id=f"server_{i}", risk_tier="UNKNOWN")
            for i in range(3)
        ]
        session.add_all(servers)

        # Create test axis scores covering the full composite range
        axis_scores = [
            # Server 1: composite > 75 (TRUSTED_GENERAL)
            McpServerRegistry(server_id="server_0", risk_tier="UNKNOWN"),
            McpLlmAxisScore(server_id="server_0", label_index=0, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=1, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=2, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=3, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=4, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=5, p_top=0.85),
            McpLlmAxisScore(server_id="server_0", label_index=6, p_top=0.85),

            # Server 2: composite > 60 (TRUSTED_RESEARCH)
            McpServerRegistry(server_id="server_1", risk_tier="UNKNOWN"),
            McpLlmAxisScore(server_id="server_1", label_index=0, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=1, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=2, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=3, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=4, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=5, p_top=0.7),
            McpLlmAxisScore(server_id="server_1", label_index=6, p_top=0.7),

            # Server 3: composite > 15 (HIGH_RISK_ISOLATED)
            McpServerRegistry(server_id="server_2", risk_tier="UNKNOWN"),
            McpLlmAxisScore(server_id="server_2", label_index=0, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=1, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=2, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=3, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=4, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=5, p_top=0.3),
            McpLlmAxisScore(server_id="server_2", label_index=6, p_top=0.3),
        ]
        session.add_all(axis_scores)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.post("/derive")
    assert response.status_code == 200
    result = response.json()

    # Verify all 3 risk tiers were derived correctly
    assert result["derived"] == 3
    assert result["updated"] == 3

    # Verify the specific risk tiers
    with TestSession() as session:
        server0 = session.query(McpServerRegistry).filter_by(server_id="server_0").first()
        server1 = session.query(McpServerRegistry).filter_by(server_id="server_1").first()
        server2 = session.query(McpServerRegistry).filter_by(server_id="server_2").first()

        assert server0.risk_tier == "TRUSTED_GENERAL"
        assert server1.risk_tier == "TRUSTED_RESEARCH"
        assert server2.risk_tier == "HIGH_RISK_ISOLATED"

    print("PASS")