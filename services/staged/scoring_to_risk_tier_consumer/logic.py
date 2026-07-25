from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, TrustGatingOverride
from typing import List, Dict, Optional
from pydantic import BaseModel

class RiskTierResult(BaseModel):
    server_id: str
    risk_tier: str
    criteria_version: str
    override_applied: bool
    axes_summary: Dict[str, float]

def compute_risk_tier(server_id: str, axis_scores: List[Dict], session: Session = Depends(get_session)) -> RiskTierResult:
    # Get trust override status
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    override = TrustGatingOverride.trust_gate(server) if server else False

    # Extract axis scores
    axes = {
        'overall_risk': 0.0,
        'auth_strength': 0.0,
        'capability_breadth': 0.0,
        'data_sensitivity': 0.0,
        'network_egress': 0.0,
        'maintainer_trust': 0.0,
        'exploit_surface': 0.0
    }

    for score in axis_scores:
        if score['axis_name'] in axes:
            axes[score['axis_name']] = score['p_top']

    # Calculate composite score
    composite = sum(axes.values()) / len(axes)

    # Check for critical axis
    critical = any(axes[axis] > 0.7 for axis in axes)

    # Determine risk tier
    if critical:
        risk_tier = 'HIGH_RISK_ISOLATED' if composite > 0.15 else 'KNOWN_THREAT'
    else:
        if composite > 0.75:
            risk_tier = 'TRUSTED_GENERAL'
        elif composite > 0.60:
            risk_tier = 'TRUSTED_RESEARCH'
        elif composite > 0.45:
            risk_tier = 'ENTERPRISE_CONTROLLED'
        elif composite > 0.30:
            risk_tier = 'CAUTION_LIMITED'
        elif composite > 0.15:
            risk_tier = 'HIGH_RISK_ISOLATED'
        else:
            risk_tier = 'KNOWN_THREAT'

    # Apply override if present
    if override:
        if risk_tier == 'TRUSTED_GENERAL':
            risk_tier = 'TRUSTED_GENERAL'
        elif risk_tier == 'TRUSTED_RESEARCH':
            risk_tier = 'TRUSTED_GENERAL'
        elif risk_tier == 'ENTERPRISE_CONTROLLED':
            risk_tier = 'TRUSTED_RESEARCH'
        elif risk_tier == 'CAUTION_LIMITED':
            risk_tier = 'ENTERPRISE_CONTROLLED'
        elif risk_tier == 'HIGH_RISK_ISOLATED':
            risk_tier = 'CAUTION_LIMITED'
        else:
            risk_tier = 'HIGH_RISK_ISOLATED'

    # Get criteria version from first axis score
    criteria_version = axis_scores[0]['decision_rule_version'] if axis_scores else 'unknown'

    return RiskTierResult(
        server_id=server_id,
        risk_tier=risk_tier,
        criteria_version=criteria_version,
        override_applied=override,
        axes_summary=axes
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        servers = [
            McpServerRegistry(server_id="server1", name="Server 1"),
            McpServerRegistry(server_id="server2", name="Server 2"),
            McpServerRegistry(server_id="server3", name="Server 3"),
            McpServerRegistry(server_id="server4", name="Server 4"),
        ]
        session.add_all(servers)

        # Create test axis scores
        axis_scores = [
            [
                {"axis_name": "overall_risk", "p_top": 0.8, "decision_rule_version": "1.0"},
                {"axis_name": "auth_strength", "p_top": 0.75, "decision_rule_version": "1.0"},
                {"axis_name": "capability_breadth", "p_top": 0.85, "decision_rule_version": "1.0"},
                {"axis_name": "data_sensitivity", "p_top": 0.7, "decision_rule_version": "1.0"},
                {"axis_name": "network_egress", "p_top": 0.8, "decision_rule_version": "1.0"},
                {"axis_name": "maintainer_trust", "p_top": 0.75, "decision_rule_version": "1.0"},
                {"axis_name": "exploit_surface", "p_top": 0.8, "decision_rule_version": "1.0"},
            ],
            [
                {"axis_name": "overall_risk", "p_top": 0.65, "decision_rule_version": "1.0"},
                {"axis_name": "auth_strength", "p_top": 0.6, "decision_rule_version": "1.0"},
                {"axis_name": "capability_breadth", "p_top": 0.7, "decision_rule_version": "1.0"},
                {"axis_name": "data_sensitivity", "p_top": 0.65, "decision_rule_version": "1.0"},
                {"axis_name": "network_egress", "p_top": 0.6, "decision_rule_version": "1.0"},
                {"axis_name": "maintainer_trust", "p_top": 0.65, "decision_rule_version": "1.0"},
                {"axis_name": "exploit_surface", "p_top": 0.6, "decision_rule_version": "1.0"},
            ],
            [
                {"axis_name": "overall_risk", "p_top": 0.5, "decision_rule_version": "1.0"},
                {"axis_name": "auth_strength", "p_top": 0.45, "decision_rule_version": "1.0"},
                {"axis_name": "capability_breadth", "p_top": 0.55, "decision_rule_version": "1.0"},
                {"axis_name": "data_sensitivity", "p_top": 0.4, "decision_rule_version": "1.0"},
                {"axis_name": "network_egress", "p_top": 0.5, "decision_rule_version": "1.0"},
                {"axis_name": "maintainer_trust", "p_top": 0.45, "decision_rule_version": "1.0"},
                {"axis_name": "exploit_surface", "p_top": 0.5, "decision_rule_version": "1.0"},
            ],
            [
                {"axis_name": "overall_risk", "p_top": 0.2, "decision_rule_version": "1.0"},
                {"axis_name": "auth_strength", "p_top": 0.15, "decision_rule_version": "1.0"},
                {"axis_name": "capability_breadth", "p_top": 0.25, "decision_rule_version": "1.0"},
                {"axis_name": "data_sensitivity", "p_top": 0.1, "decision_rule_version": "1.0"},
                {"axis_name": "network_egress", "p_top": 0.2, "decision_rule_version": "1.0"},
                {"axis_name": "maintainer_trust", "p_top": 0.15, "decision_rule_version": "1.0"},
                {"axis_name": "exploit_surface", "p_top": 0.2, "decision_rule_version": "1.0"},
            ],
        ]

        # Test each server
        for i, server in enumerate(servers):
            result = compute_risk_tier(server.server_id, axis_scores[i], session)
            print(f"Server {server.server_id}: {result.risk_tier}")

        # Verify results
        assert servers[0].server_id == "server1"
        assert servers[1].server_id == "server2"
        assert servers[2].server_id == "server3"
        assert servers[3].server_id == "server4"

        print("PASS")
    finally:
        session.close()