from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from services.staged.trust_gate_preview_api.logic import (
        compute_trust_gate,
        select_latest_axis_labels,
    )
except ImportError:
    from .logic import compute_trust_gate, select_latest_axis_labels

router = APIRouter(prefix="/api/trust-gate", tags=["trust-gate"])


@router.get("/preview")
def preview_trust_gate(
    server_id: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if server_id is None:
        return {
            "server_id": None,
            "gate": None,
            "error": "unknown_server",
        }

    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if server is None:
        return {
            "server_id": None,
            "gate": None,
            "error": "unknown_server",
        }

    axis_rows = session.execute(
        select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    ).scalars().all()

    axes = select_latest_axis_labels(axis_rows)
    gate = compute_trust_gate(
        url=server.url,
        name=server.name,
        axes=axes,
    )

    return {
        "server_id": server_id,
        "gate": gate,
        "axes": axes,
        "tier": server.risk_tier,
        "verdict": server.verdict,
    }