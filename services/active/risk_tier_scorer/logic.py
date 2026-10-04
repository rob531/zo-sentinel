# deps: fastapi, pydantic, sqlalchemy
"""logic.py -- pure scoring functions, no FastAPI wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


# ---------------------------------------------------------------------------
# Risk tier thresholds
# ---------------------------------------------------------------------------
_TIER_CRITICAL = 80.0
_TIER_HIGH = 60.0
_TIER_MEDIUM = 40.0
_TIER_LOW = 20.0


def _assign_tier(score: float) -> str:
    if score >= _TIER_CRITICAL:
        return "CRITICAL"
    if score >= _TIER_HIGH:
        return "HIGH"
    if score >= _TIER_MEDIUM:
        return "MEDIUM"
    if score >= _TIER_LOW:
        return "LOW"
    return "MINIMAL"


# ---------------------------------------------------------------------------
# Pydantic-light result dataclass (no FastAPI dep in logic)
# ---------------------------------------------------------------------------
@dataclass
class AxisScoreItem:
    axis_name: str
    label_index: int
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


@dataclass
class RiskScoreResult:
    server_id: str
    server_name: Optional[str]
    overall_score: float
    confidence: float
    risk_tier: str
    axes: List[Dict[str, Any]]
    evidence: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------
def compute_risk_score(session: Session, server_id: str) -> RiskScoreResult:
    """Compute overall risk score for a server based on axis scores.

    Weighted average: p_top weighted by (p_critical * 3 + p_danger * 2).
    Escalated axes receive +10 bonus toward overall score.
    """
    axis_scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    server_name = server.name if server else None

    if not axis_scores:
        return RiskScoreResult(
            server_id=server_id,
            server_name=server_name,
            overall_score=0.0,
            confidence=0.0,
            risk_tier="UNKNOWN",
            axes=[],
            evidence={"error": "No axis scores found for server"},
        )

    total_weighted_score = 0.0
    total_weight = 0.0
    escalated_count = 0
    axes: List[Dict[str, Any]] = []

    for s in axis_scores:
        weight = s.p_critical * 3.0 + s.p_danger * 2.0 + 0.01
        total_weighted_score += s.p_top * weight * 100.0
        total_weight += weight
        if s.escalated:
            escalated_count += 1
        axes.append({
            "axis_name": s.axis_name,
            "label_index": s.label_index,
            "label": s.label,
            "p_top": s.p_top,
            "p_critical": s.p_critical,
            "p_danger": s.p_danger,
            "escalated": s.escalated,
            "weight": weight,
        })

    base_score = (total_weighted_score / total_weight) if total_weight > 0 else 0.0
    escalation_bonus = min(escalated_count * 5.0, 25.0)
    overall_score = min(base_score + escalation_bonus, 100.0)

    confidence = max(0.0, min(1.0, len(axis_scores) / 10.0))
    risk_tier = _assign_tier(overall_score)

    return RiskScoreResult(
        server_id=server_id,
        server_name=server_name,
        overall_score=round(overall_score, 2),
        confidence=round(confidence, 3),
        risk_tier=risk_tier,
        axes=axes,
        evidence={
            "axes_analyzed": len(axis_scores),
            "escalated_axes": escalated_count,
            "escalation_bonus": round(escalation_bonus, 2),
        },
    )


def batch_risk_scores(session: Session, server_ids: List[str]) -> List[RiskScoreResult]:
    """Compute risk scores for multiple servers."""
    return [compute_risk_score(session, sid) for sid in server_ids]
