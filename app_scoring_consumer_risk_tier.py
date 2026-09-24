import requests
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from fastapi import Depends
from sqlalchemy.orm import Session

def compute_risk_tier_from_axes(server_id: str, axes: List[Dict]) -> Optional[str]:
    if not axes:
        return None

    # Check for KNOWN_THREAT condition
    for axis in axes:
        if axis['p_critical'] > 0.5:
            return "KNOWN_THREAT"

    # Check if we have enough axes
    if len(axes) < 5:
        return "INSUFFICIENT"

    # Calculate composite score
    overall_risk = next((axis for axis in axes if axis['axis_name'] == 'overall_risk'), None)
    if not overall_risk:
        return "INSUFFICIENT"

    # Apply overall_risk escalation if needed
    if overall_risk['label_index'] >= 4:
        escalate = True
    else:
        escalate = False

    # Calculate weighted composite score
    weights = {
        'overall_risk': 0.4,
        'auth_strength': 0.1,
        'capability_breadth': 0.1,
        'data_sensitivity': 0.1,
        'network_egress': 0.1,
        'maintainer_trust': 0.1,
        'exploit_surface': 0.1
    }

    composite = 0.0
    for axis in axes:
        if axis['axis_name'] in weights:
            composite += axis['p_top'] * weights[axis['axis_name']]

    # Apply escalation
    if escalate:
        composite *= 1.1  # 10% escalation

    # Determine tier
    if composite > 0.75 and len(axes) >= 6:
        return "TRUSTED_GENERAL"
    elif composite > 0.60:
        return "TRUSTED_RESEARCH"
    elif composite > 0.45:
        return "ENTERPRISE_CONTROLLED"
    elif composite > 0.30:
        return "CAUTION_LIMITED"
    elif composite > 0.15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def run():
    # Get all servers that need risk tier updates
    session = get_session()
    servers = session.query(McpServerRegistry).all()

    for server in servers:
        # Get all axes for this server
        axes = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server.id
        ).all()

        # Convert to dict format expected by compute_risk_tier_from_axes
        axes_dict = [{
            'axis_name': axis.axis_name,
            'p_top': axis.p_top,
            'p_critical': axis.p_critical,
            'label': axis.label,
            'label_index': axis.label_index
        } for axis in axes]

        # Compute risk tier
        tier = compute_risk_tier_from_axes(server.id, axes_dict)

        if tier:
            # Update server registry
            server.risk_tier = tier
            session.commit()

            # Notify write_service
            requests.post(
                "http://127.0.0.1:8772/update",
                json={
                    "table": "mcp_server_registry",
                    "id": server.id,
                    "field": "risk_tier",
                    "value": tier
                }
            )

if __name__ == "__main__":
    # Self-test with known cases
    test_cases = [
        {
            "server_id": "server1",
            "axes": [
                {"axis_name": "overall_risk", "p_top": 0.8, "p_critical": 0.1, "label": "high", "label_index": 3},
                {"axis_name": "auth_strength", "p_top": 0.9, "p_critical": 0.1, "label": "low", "label_index": 1},
                {"axis_name": "capability_breadth", "p_top": 0.85, "p_critical": 0.1, "label": "medium", "label_index": 2},
                {"axis_name": "data_sensitivity", "p_top": 0.75, "p_critical": 0.1, "label": "medium", "label_index": 2},
                {"axis_name": "network_egress", "p_top": 0.8, "p_critical": 0.1, "label": "medium", "label_index": 2},
                {"axis_name": "maintainer_trust", "p_top": 0.9, "p_critical": 0.1, "label": "low", "label_index": 1},
                {"axis_name": "exploit_surface", "p_top": 0.8, "p_critical": 0.1, "label": "medium", "label_index": 2}
            ],
            "expected_tier": "TRUSTED_GENERAL"
        },
        {
            "server_id": "server2",
            "axes": [
                {"axis_name": "overall_risk", "p_top": 0.5, "p_critical": 0.6, "label": "critical", "label_index": 4},
                {"axis_name": "auth_strength", "p_top": 0.9, "p_critical": 0.1, "label": "low", "label_index": 1}
            ],
            "expected_tier": "KNOWN_THREAT"
        },
        {
            "server_id": "server3",
            "axes": [
                {"axis_name": "overall_risk", "p_top": 0.3, "p_critical": 0.1, "label": "low", "label_index": 1},
                {"axis_name": "auth_strength", "p_top": 0.4, "p_critical": 0.1, "label": "low", "label_index": 1}
            ],
            "expected_tier": "INSUFFICIENT"
        }
    ]

    for i, test_case in enumerate(test_cases):
        server_id = test_case["server_id"]
        axes = test_case["axes"]
        expected_tier = test_case["expected_tier"]

        tier = compute_risk_tier_from_axes(server_id, axes)
        assert tier == expected_tier, f"Test failed for server {server_id}"

    print("PASS")