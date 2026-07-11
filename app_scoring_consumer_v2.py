from typing import Dict, Literal
from fastapi import Depends
from app.db import get_session
from app.models import MCPLlmAxisScores, MCPServerRegistry
from app.trust_gating_override import trust_gate
from sqlalchemy.orm import Session

RiskTier = Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK_ISOLATED", "HIGH_RISK_QUARANTINED"]

def compute_risk_tier(server_id: int, session: Session = Depends(get_session)) -> RiskTier:
    # Fetch the axis scores for the given server_id
    axis_scores = session.query(MCPLlmAxisScores).filter(MCPLlmAxisScores.server_id == server_id).first()

    if not axis_scores:
        raise ValueError(f"No axis scores found for server_id: {server_id}")

    # Extract the scores for each axis
    scores = {
        "CRITICAL": axis_scores.critical,
        "HIGH": axis_scores.high,
        "MEDIUM": axis_scores.medium,
        "LOW": axis_scores.low,
        "INFORMATIONAL": axis_scores.informational,
        "UNKNOWN": axis_scores.unknown,
        "TRUST_GATE": axis_scores.trust_gate
    }

    # Apply trust gating rules
    tier = trust_gate(scores)

    return tier

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    def get_test_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = get_test_session

    # Create test data
    test_server_id = 1
    test_server = MCPServerRegistry(
        id=test_server_id,
        name="test_server",
        org_id=1,
        user_id=1,
        created_at="2023-01-01",
        updated_at="2023-01-01"
    )

    test_axis_scores = MCPLlmAxisScores(
        server_id=test_server_id,
        critical=0.9,
        high=0.8,
        medium=0.7,
        low=0.6,
        informational=0.5,
        unknown=0.4,
        trust_gate=0.3
    )

    with SessionLocal() as session:
        session.add(test_server)
        session.add(test_axis_scores)
        session.commit()

    # Test cases
    test_cases = [
        (1, "HIGH_RISK_ISOLATED"),  # CRITICAL axis forces HIGH_RISK_ISOLATED
        (2, "MEDIUM_RISK"),        # Example of a medium risk tier
    ]

    # Add another test server with different scores
    test_server_id_2 = 2
    test_server_2 = MCPServerRegistry(
        id=test_server_id_2,
        name="test_server_2",
        org_id=1,
        user_id=1,
        created_at="2023-01-01",
        updated_at="2023-01-01"
    )

    test_axis_scores_2 = MCPLlmAxisScores(
        server_id=test_server_id_2,
        critical=0.1,
        high=0.2,
        medium=0.7,
        low=0.6,
        informational=0.5,
        unknown=0.4,
        trust_gate=0.3
    )

    with SessionLocal() as session:
        session.add(test_server_2)
        session.add(test_axis_scores_2)
        session.commit()

    # Run tests
    for server_id, expected_tier in test_cases:
        tier = compute_risk_tier(server_id)
        assert tier == expected_tier, f"Test failed for server_id {server_id}. Expected {expected_tier}, got {tier}"

    print("PASS")