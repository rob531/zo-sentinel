from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scores", tags=["scores"])


@router.get("/history")
def get_scores_history(
    server_id: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.label,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(
            McpLlmAxisScore.axis_name.asc(),
            McpLlmAxisScore.scored_at.asc(),
        )
    )

    rows = session.execute(statement).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    server_name = rows[0].name
    current_risk_tier = rows[0].risk_tier
    grouped: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        axis_name = row.axis_name
        grouped.setdefault(axis_name, []).append(
            {
                "scored_at": row.scored_at,
                "p_top": row.p_top,
                "p_critical": row.p_critical,
                "p_danger": row.p_danger,
                "escalated": bool(row.escalated),
                "escalated_to": row.escalated_to,
                "label": row.label,
            }
        )

    axis_names = sorted(grouped)
    series = [
        {
            "axis_name": axis_name,
            "points": grouped[axis_name],
        }
        for axis_name in axis_names
    ]

    return {
        "server_id": server_id,
        "server_name": server_name,
        "current_risk_tier": current_risk_tier,
        "days": days,
        "series": series,
        "axes": axis_names,
    }


from sqlalchemy import select


if __name__ == "__main__":
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None
    print("PASS")