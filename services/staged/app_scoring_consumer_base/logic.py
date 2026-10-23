"""
services/staged/app_scoring_consumer_base/logic.py

Base scoring‑consumer logic shared by all axis‑level risk scoring consumers.
Provides helpers for reading axis scores, weighting them, and deriving a
risk‑tier string from a collection of axis contributions.
"""

from __future__ import annotations

from typing import List, Dict

import requests  # noqa: F401 – retained for potential external calls

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Configuration – in‑code defaults (can be overridden by callers if needed)
# --------------------------------------------------------------------------- #

# Default per‑axis weight.  Real deployments may replace this mapping via
# environment variables or a config service; the fallback is 1.0 for any
# unknown axis.
_AXIS_WEIGHTS: Dict[str, float] = {
    "security": 1.5,
    "performance": 1.2,
    "reliability": 1.3,
    "compliance": 1.4,
    "availability": 1.1,
}

# Simple tier thresholds based on a normalized composite score (0‑1).
# Adjust as required by business rules.
_TIER_THRESHOLDS: List[tuple[float, str]] = [
    (0.33, "low"),
    (0.66, "medium"),
    (1.00, "high"),
]


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #
def get_axis_weight(axis_name: str) -> float:
    """
    Return the configured weight for ``axis_name``.
    If the axis is not present in the weight map, a default weight of 1.0 is used.
    """
    return _AXIS_WEIGHTS.get(axis_name, 1.0)


def fetch_axis_scores(session, server_id: str) -> List[Dict]:
    """
    Retrieve LLM axis scores for a given ``server_id`` from the
    ``mcp_llm_axis_scores`` table.

    The returned list contains dictionaries with the minimal fields required
    by the scoring logic:

        {
            "axis_name": str,
            "p_critical": float,
        }

    Parameters
    ----------
    session: SQLAlchemy session (provided by ``app.db.get_session``)
    server_id: The identifier of the server whose axis scores are requested.

    Returns
    -------
    List[Dict] – one entry per axis score.
    """
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return [
        {"axis_name": row.axis_name, "p_critical": float(row.p_critical)}
        for row in rows
        if row.p_critical is not None
    ]


def _compute_composite_score(axes: List[Dict]) -> float:
    """
    Compute a normalized composite score from a list of axis contributions.
    Each axis contributes ``weight * p_critical``; the sum is divided by the
    total weight to keep the result in the 0‑1 range.

    Parameters
    ----------
    axes: List of dictionaries each containing ``axis_name`` and ``p_critical``.

    Returns
    -------
    float – composite score in the range [0, 1].
    """
    if not axes:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0
    for axis in axes:
        name = axis.get("axis_name")
        p_crit = axis.get("p_critical", 0.0)
        weight = get_axis_weight(name)
        weighted_sum += weight * p_crit
        total_weight += weight

    return weighted_sum / total_weight if total_weight else 0.0


def compute_risk_tier_from_axes(axes: List[Dict]) -> str:
    """
    Derive a risk‑tier label from a collection of axis scores.

    The function first builds a composite score (0‑1) and then maps that
    score onto a tier using the ``_TIER_THRESHOLDS`` table.

    Parameters
    ----------
    axes: List of dictionaries with at least ``axis_name`` and ``p_critical``.

    Returns
    -------
    str – one of ``"low"``, ``"medium"``, or ``"high"``.  Returns an empty
    string only if the input list is empty (which should not happen in normal
    operation).
    """
    if not axes:
        return ""

    composite = _compute_composite_score(axes)

    for threshold, tier in _TIER_THRESHOLDS:
        if composite <= threshold:
            return tier
    return ""  # Fallback – should never be reached with the thresholds above


# --------------------------------------------------------------------------- #
# Optional convenience wrappers used by other services
# --------------------------------------------------------------------------- #
def get_server_registry(session, server_id: str) -> McpServerRegistry | None:
    """
    Fetch the ``McpServerRegistry`` row for ``server_id``.
    Returns ``None`` if the server is not present.
    """
    return (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .one_or_none()
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Known deterministic input – the exact values are not important,
    # only that a non‑empty tier string is produced.
    test_axes = [
        {"axis_name": "security", "p_critical": 0.9},
        {"axis_name": "performance", "p_critical": 0.2},
        {"axis_name": "reliability", "p_critical": 0.5},
    ]

    tier = compute_risk_tier_from_axes(test_axes)
    assert isinstance(tier, str) and tier, "Risk tier must be a non‑empty string"
    print("PASS")