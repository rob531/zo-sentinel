from typing import List

from fastapi import Depends
from app.db import get_session
from app.models import McpLlmAxisScore


def compute_risk_tier(scores: List[float]) -> str:
    """
    Compute a risk tier based on a list of numeric scores.
    The average of the scores is used for tier determination.

    Tiers:
        TRUSTED_GENERAL          > 75
        TRUSTED_RESEARCH         > 60
        ENTERPRISE_CONTROLLED   > 45
        CAUTION_LIMITED         > 30
        HIGH_RISK_ISOLATED      > 15
        KNOWN_THREAT            <= 15
    """
    if not scores:
        avg = 0.0
    else:
        avg = sum(scores) / len(scores)

    if avg > 75:
        return "TRUSTED_GENERAL"
    if avg > 60:
        return "TRUSTED_RESEARCH"
    if avg > 45:
        return "ENTERPRISE_CONTROLLED"
    if avg > 30:
        return "CAUTION_LIMITED"
    if avg > 15:
        return "HIGH_RISK_ISOLATED"
    return "KNOWN_THREAT"


def fetch_axis_scores(server_id: int, session=Depends(get_session)) -> List[float]:
    """
    Retrieve the `p_top` scores for a given server from the
    `mcp_llm_axis_scores` table.
    """
    records = (
        session.query(McpLlmAxisScore.p_top)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return [r[0] for r in records if r[0] is not None]


if __name__ == "__main__":
    # Self‑test cases
    test_cases = [
        ([80, 90], "TRUSTED_GENERAL"),
        ([65, 70], "TRUSTED_RESEARCH"),
        ([50, 48], "ENTERPRISE_CONTROLLED"),
        ([35, 32], "CAUTION_LIMITED"),
        ([20, 18], "HIGH_RISK_ISOLATED"),
        ([10, 15], "KNOWN_THREAT"),
        ([], "KNOWN_THREAT"),
    ]

    for scores, expected in test_cases:
        result = compute_risk_tier(scores)
        assert result == expected, f"Expected {expected} for {scores}, got {result}"

    print("PASS")