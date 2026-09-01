from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from services.staged.trust_gating_override.logic import trust_gate

def get_discrepancies(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Compute risk tiers from axis scores and compare with trust gate overrides."""
    # Query the latest scores and server info
    query = db.query(
        McpServerRegistry.id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.last_scored,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_medium,
        McpLlmAxisScore.p_low,
        McpLlmAxisScore.p_unknown,
        McpLlmAxisScore.p_critical,
    ).join(
        McpServerRegistry,
        McpServerRegistry.id == McpLlmAxisScore.server_id
    ).order_by(
        McpServerRegistry.last_scored.desc()
    ).all()

    discrepancies = []

    for row in query:
        # Compute the overall risk tier from the top-1 axis
        if row.p_top > 0.5:
            computed_tier = "HIGH_RISK"
        elif row.p_medium > 0.5:
            computed_tier = "MEDIUM_RISK"
        elif row.p_low > 0.5:
            computed_tier = "LOW_RISK"
        elif row.p_unknown > 0.5:
            computed_tier = "UNKNOWN_RISK"
        elif row.p_critical > 0.5:
            computed_tier = "CRITICAL_RISK"
        else:
            computed_tier = "TRUSTED"

        # Get the trust gate override
        override_trusted = trust_gate(row.url, row.name, {})

        # Determine the override's implied tier
        override_tier = "TRUSTED" if override_trusted else "HIGH_RISK"

        # Check for discrepancy
        if computed_tier != override_tier:
            discrepancies.append({
                "server_id": row.id,
                "name": row.name,
                "computed_tier": computed_tier,
                "override_trusted": override_trusted,
                "url": row.url,
                "last_scored": row.last_scored
            })

    return discrepancies

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as get_real_session

    # Mock trust_gate for testing
    def mock_trust_gate(url: str, name: str, context: Dict[str, Any]) -> bool:
        return url in ["http://trusted1.com", "http://trusted2.com"]

    # Override trust_gate for testing
    from services.staged.trust_gating_override.logic import trust_gate as real_trust_gate
    app.dependency_overrides[real_trust_gate] = mock_trust_gate

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override get_session for testing
    app.dependency_overrides[get_real_session] = lambda: SessionLocal()

    # Seed test data
    from app.models import McpServerRegistry, McpLlmAxisScore
    session = SessionLocal()

    # Seed 5 servers: 2 with computed tier HIGH_RISK, 3 TRUSTED
    servers = [
        {"id": 1, "name": "Server 1", "url": "http://trusted1.com", "last_scored": "2023-01-01"},
        {"id": 2, "name": "Server 2", "url": "http://trusted2.com", "last_scored": "2023-01-02"},
        {"id": 3, "name": "Server 3", "url": "http://untrusted1.com", "last_scored": "2023-01-03"},
        {"id": 4, "name": "Server 4", "url": "http://untrusted2.com", "last_scored": "2023-01-04"},
        {"id": 5, "name": "Server 5", "url": "http://trusted3.com", "last_scored": "2023-01-05"},
    ]

    for server in servers:
        session.add(McpServerRegistry(**server))

    scores = [
        {"server_id": 1, "p_top": 0.1, "p_medium": 0.1, "p_low": 0.1, "p_unknown": 0.1, "p_critical": 0.6},
        {"server_id": 2, "p_top": 0.1, "p_medium": 0.1, "p_low": 0.1, "p_unknown": 0.1, "p_critical": 0.6},
        {"server_id": 3, "p_top": 0.6, "p_medium": 0.1, "p_low": 0.1, "p_unknown": 0.1, "p_critical": 0.1},
        {"server_id": 4, "p_top": 0.6, "p_medium": 0.1, "p_low": 0.1, "p_unknown": 0.1, "p_critical": 0.1},
        {"server_id": 5, "p_top": 0.1, "p_medium": 0.1, "p_low": 0.1, "p_unknown": 0.1, "p_critical": 0.6},
    ]

    for score in scores:
        session.add(McpLlmAxisScore(**score))

    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/verdict/override-discrepancy")
    assert response.status_code == 200
    data = response.json()
    assert "discrepancies" in data
    assert len(data["discrepancies"]) == 3  # 3 discrepancies expected

    print("PASS")