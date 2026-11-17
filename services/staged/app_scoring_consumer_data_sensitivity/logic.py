from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAME = "data_sensitivity"


def _unscored_result(
    server_id: str,
    registry: McpServerRegistry,
) -> Dict[str, Any]:
    return {
        "server_id": server_id,
        "name": registry.name,
        "verdict": registry.verdict,
        "risk_tier": registry.risk_tier,
        "trust_score": registry.trust_score,
        "confidence": registry.confidence,
        "last_assessed": registry.last_assessed,
        "axis": AXIS_NAME,
        "scored": False,
        "label": None,
        "label_index": None,
        "probs": None,
        "p_top": None,
        "p_critical": None,
        "p_danger": None,
        "escalated": None,
        "escalated_to": None,
        "decision_rule_version": None,
        "model_version": None,
        "adapter_sha256": None,
        "scored_at": None,
    }


def get_data_sensitivity(
    session: Any = Depends(get_session),
    server_id: str = "",
) -> Dict[str, Any]:
    if isinstance(session, str) and not isinstance(server_id, str):
        session, server_id = server_id, session

    if not server_id:
        raise HTTPException(status_code=404, detail="Server not found")

    row = (
        session.query(McpServerRegistry, McpLlmAxisScore)
        .outerjoin(
            McpLlmAxisScore,
            (McpLlmAxisScore.server_id == McpServerRegistry.server_id)
            & (McpLlmAxisScore.axis_name == AXIS_NAME),
        )
        .filter(McpServerRegistry.server_id == server_id)
        .order_by(
            McpLlmAxisScore.scored_at.desc().nullslast(),
            McpLlmAxisScore.id.desc(),
        )
        .first()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Server not found")

    registry, score = row

    if score is None:
        return _unscored_result(server_id, registry)

    return {
        "server_id": server_id,
        "name": registry.name,
        "verdict": registry.verdict,
        "risk_tier": registry.risk_tier,
        "trust_score": registry.trust_score,
        "confidence": registry.confidence,
        "last_assessed": registry.last_assessed,
        "axis": AXIS_NAME,
        "scored": True,
        "label": score.label,
        "label_index": score.label_index,
        "probs": score.probs,
        "p_top": score.p_top,
        "p_critical": score.p_critical,
        "p_danger": score.p_danger,
        "escalated": score.escalated,
        "escalated_to": score.escalated_to,
        "decision_rule_version": score.decision_rule_version,
        "model_version": score.model_version,
        "adapter_sha256": score.adapter_sha256,
        "scored_at": score.scored_at,
    }


get_data_sensitivity_score = get_data_sensitivity
get_consumer_data_sensitivity = get_data_sensitivity
get_data_sensitivity_axis_score = get_data_sensitivity