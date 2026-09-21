from typing import List, Dict, Optional
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
import requests

class RiskTierTransitionMatrixResponse(BaseModel):
    matrix: List[Dict[str, str | int]]
    total_transitions: int
    period_days: int

def get_risk_tier_transition_matrix(
    session: Session = Depends(get_session),
    period_days: int = 30
) -> RiskTierTransitionMatrixResponse:
    # Get the current date and the start date for the period
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=period_days)

    # Query to get the risk scores for each server in the period
    query = session.query(
        McpServerRegistry.server_id,
        McpLlmAxisScore.overall_risk,
        McpLlmAxisScore.scored_at
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).order_by(
        McpServerRegistry.server_id,
        McpLlmAxisScore.scored_at
    ).all()

    # Define the risk tier thresholds
    thresholds = [
        (75, "TRUSTED_GENERAL"),
        (60, "TRUSTED_RESEARCH"),
        (45, "ENTERPRISE_CONTROLLED"),
        (30, "CAUTION_LIMITED"),
        (15, "HIGH_RISK_ISOLATED"),
        (0, "KNOWN_THREAT")
    ]

    # Initialize variables to track transitions
    transitions = []
    previous_tier = None
    previous_server_id = None

    for row in query:
        server_id, overall_risk, scored_at = row
        # Determine the risk tier based on the overall_risk score
        tier = None
        for threshold, tier_name in thresholds:
            if overall_risk >= threshold:
                tier = tier_name
                break

        if tier is None:
            tier = "KNOWN_THREAT"

        # Check if the server_id has changed or if it's the first row
        if server_id != previous_server_id:
            previous_tier = tier
            previous_server_id = server_id
        else:
            # Check if the tier has changed
            if tier != previous_tier:
                transitions.append({
                    "from_tier": previous_tier,
                    "to_tier": tier,
                    "server_id": server_id,
                    "scored_at": scored_at
                })
                previous_tier = tier

    # Count the transitions
    matrix = []
    for transition in transitions:
        found = False
        for entry in matrix:
            if entry["from_tier"] == transition["from_tier"] and entry["to_tier"] == transition["to_tier"]:
                entry["count"] += 1
                found = True
                break
        if not found:
            matrix.append({
                "from_tier": transition["from_tier"],
                "to_tier": transition["to_tier"],
                "count": 1
            })

    return RiskTierTransitionMatrixResponse(
        matrix=matrix,
        total_transitions=len(transitions),
        period_days=period_days
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    test_session = SessionLocal()
    test_session.add_all([
        McpServerRegistry(server_id="server1"),
        McpServerRegistry(server_id="server2"),
        McpServerRegistry(server_id="server3"),
        McpServerRegistry(server_id="server4"),
        McpServerRegistry(server_id="server5"),
    ])
    test_session.commit()

    test_session.add_all([
        McpLlmAxisScore(server_id="server1", overall_risk=80, scored_at=datetime.utcnow() - timedelta(days=2)),
        McpLlmAxisScore(server_id="server1", overall_risk=70, scored_at=datetime.utcnow() - timedelta(days=1)),
        McpLlmAxisScore(server_id="server1", overall_risk=65, scored_at=datetime.utcnow()),
        McpLlmAxisScore(server_id="server2", overall_risk=50, scored_at=datetime.utcnow() - timedelta(days=2)),
        McpLlmAxisScore(server_id="server2", overall_risk=40, scored_at=datetime.utcnow() - timedelta(days=1)),
        McpLlmAxisScore(server_id="server2", overall_risk=35, scored_at=datetime.utcnow()),
        McpLlmAxisScore(server_id="server3", overall_risk=25, scored_at=datetime.utcnow() - timedelta(days=2)),
        McpLlmAxisScore(server_id="server3", overall_risk=20, scored_at=datetime.utcnow() - timedelta(days=1)),
        McpLlmAxisScore(server_id="server3", overall_risk=10, scored_at=datetime.utcnow()),
        McpLlmAxisScore(server_id="server4", overall_risk=10, scored_at=datetime.utcnow() - timedelta(days=2)),
        McpLlmAxisScore(server_id="server4", overall_risk=5, scored_at=datetime.utcnow() - timedelta(days=1)),
        McpLlmAxisScore(server_id="server4", overall_risk=0, scored_at=datetime.utcnow()),
        McpLlmAxisScore(server_id="server5", overall_risk=90, scored_at=datetime.utcnow() - timedelta(days=2)),
        McpLlmAxisScore(server_id="server5", overall_risk=85, scored_at=datetime.utcnow() - timedelta(days=1)),
        McpLlmAxisScore(server_id="server5", overall_risk=80, scored_at=datetime.utcnow()),
    ])
    test_session.commit()

    # Override the dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Test the function
    response = get_risk_tier_transition_matrix(period_days=2)
    assert response.total_transitions >= 3
    assert len(response.matrix) >= 3
    print("PASS")