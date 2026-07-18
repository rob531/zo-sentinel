import requests
from typing import Dict
from app.db import get_session
from app.models import MCPLLMAxisScores
from fastapi import Depends
from sqlalchemy.orm import Session

def compute_axis_summary(server_id: str) -> Dict[str, float]:
    """Aggregates the top probability scores for each risk axis of a given MCP server.

    Args:
        server_id: String identifier for the target server in mcp_llm_axis_scores.

    Returns:
        Dictionary mapping each axis_name to its p_top float value.
    """
    axis_names = [
        'overall_risk', 'auth_strength', 'capability_breadth',
        'data_sensitivity', 'network_egress', 'maintainer_trust',
        'exploit_surface'
    ]

    session: Session = Depends(get_session)()
    try:
        scores = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.server_id == server_id
        ).all()

        summary = {}
        for axis in axis_names:
            axis_scores = [s.p_top for s in scores if s.axis_name == axis]
            if axis_scores:
                summary[axis] = max(axis_scores)
            else:
                summary[axis] = 0.0

        return summary
    finally:
        session.close()

if __name__ == "__main__":
    # Self-test with a known test server_id
    test_server_id = "test-server-123"
    summary = compute_axis_summary(test_server_id)

    print(summary)

    # Assertions for acceptance criteria
    assert len(summary) == 7, "Dictionary must contain exactly 7 keys"
    for value in summary.values():
        assert isinstance(value, float), "All values must be floats"
        assert 0 <= value <= 1, "All values must be between 0 and 1"

    print("PASS")