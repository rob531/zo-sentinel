"""
services/staged/risk_tier_transformer/logic.py

Transforms per‑axis LLM scores into a canonical risk tier and persists the
result to the server registry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

import requests
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Deterministic scoring rule
# --------------------------------------------------------------------------- #
# The rule aggregates the `p_top` probability for each of the (up to) seven
# axes.  The average of those probabilities is mapped onto a six‑tier
# risk classification.
# --------------------------------------------------------------------------- #
_TIER_MAP = [
    (0.90, "TRUSTED_GENERAL"),
    (0.75, "TRUSTED_RESEARCH"),
    (0.60, "ENTERPRISE_CONTROLLED"),
    (0.45, "CAUTION_LIMITED"),
    (0.30, "HIGH_RISK_ISOLATED"),
    (0.00, "INSUFFICIENT"),
]


def _map_score_to_tier(avg_score: float) -> str:
    """Return the tier label for a given average `p_top` score."""
    for threshold, tier in _TIER_MAP:
        if avg_score >= threshold:
            return tier
    # Fallback – should never happen because the last entry catches all.
    return "INSUFFICIENT"


def compute_risk_tier(
    server_id: str, axis_scores: List[Dict]
) -> Tuple[str, Dict]:
    """
    Compute the risk tier for a server.

    Parameters
    ----------
    server_id: str
        Identifier of the server (unused in the calculation but kept for
        signature compatibility).
    axis_scores: list[dict]
        Each dict corresponds to a row from ``mcp_llm_axis_scores`` and must
        contain at least the keys ``axis_name``, ``p_top`` and
        ``decision_rule_version``.

    Returns
    -------
    tuple[str, dict]
        ``risk_tier`` – the canonical tier label.
        ``evidence`` – a mapping ``axis_name -> {p_top, decision_rule_version}``.
    """
    if not axis_scores:
        # No data – treat as insufficient.
        return "INSUFFICIENT", {}

    total = 0.0
    evidence: Dict[str, Dict] = {}
    for row in axis_scores:
        p_top = float(row.get("p_top", 0.0))
        total += p_top
        evidence[row["axis_name"]] = {
            "p_top": p_top,
            "decision_rule_version": row.get("decision_rule_version"),
        }

    avg_score = total / len(axis_scores)
    tier = _map_score_to_tier(avg_score)
    return tier, evidence


def _persist_risk_tier(
    session: Session, server_id: str, tier: str
) -> None:
    """
    Write the computed tier back to ``mcp_server_registry``.
    """
    stmt = (
        update(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .values(risk_tier=tier, last_assessed=datetime.utcnow())
    )
    session.execute(stmt)


def _gather_axis_scores(
    session: Session, server_id: str
) -> List[Dict]:
    """
    Pull all axis score rows for a given server.
    """
    stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.decision_rule_version,
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name)
    )
    rows = session.execute(stmt).all()
    return [
        {
            "axis_name": r.axis_name,
            "p_top": r.p_top,
            "decision_rule_version": r.decision_rule_version,
        }
        for r in rows
    ]


def process_pending(batch_size: int = 500) -> None:
    """
    Batch‑process all servers that have axis scores but no risk tier yet.

    The function is idempotent – servers that already have a tier are skipped.
    """
    with get_session() as session:
        # Identify servers lacking a tier.
        subq = (
            select(McpServerRegistry.server_id)
            .where(McpServerRegistry.risk_tier.is_(None))
            .subquery()
        )
        stmt = (
            select(McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.server_id.in_(subq))
            .group_by(McpLlmAxisScore.server_id)
            .limit(batch_size)
        )
        server_ids = [r.server_id for r in session.execute(stmt).all()]

        for sid in server_ids:
            axis_scores = _gather_axis_scores(session, sid)
            tier, _ = compute_risk_tier(sid, axis_scores)
            _persist_risk_tier(session, sid, tier)

        session.commit()


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Four synthetic servers, each designed to land in a distinct tier.
    test_cases = [
        {
            "server_id": "srv_trusted_general",
            "axis_scores": [
                {"axis_name": f"axis_{i}", "p_top": 0.95, "decision_rule_version": "v1"}
                for i in range(7)
            ],
            "expected_tier": "TRUSTED_GENERAL",
        },
        {
            "server_id": "srv_trusted_research",
            "axis_scores": [
                {"axis_name": f"axis_{i}", "p_top": 0.80, "decision_rule_version": "v1"}
                for i in range(7)
            ],
            "expected_tier": "TRUSTED_RESEARCH",
        },
        {
            "server_id": "srv_enterprise_controlled",
            "axis_scores": [
                {"axis_name": f"axis_{i}", "p_top": 0.65, "decision_rule_version": "v1"}
                for i in range(7)
            ],
            "expected_tier": "ENTERPRISE_CONTROLLED",
        },
        {
            "server_id": "srv_insufficient",
            "axis_scores": [
                {"axis_name": f"axis_{i}", "p_top": 0.10, "decision_rule_version": "v1"}
                for i in range(7)
            ],
            "expected_tier": "INSUFFICIENT",
        },
    ]

    for case in test_cases:
        tier, evidence = compute_risk_tier(
            case["server_id"], case["axis_scores"]
        )
        assert tier == case["expected_tier"], f"{case['server_id']} -> {tier}"
        # Ensure evidence contains all axes.
        assert len(evidence) == 7, f"evidence missing for {case['server_id']}"

    # Simulate a simple batch loop over the four servers.
    processed = 0
    for case in test_cases:
        tier, _ = compute_risk_tier(case["server_id"], case["axis_scores"])
        assert tier == case["expected_tier"]
        processed += 1

    assert processed == 4
    print("PASS")