"""HTTP surface for the read-only LLM-axis escalation audit service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_escalations

router = APIRouter(prefix="/api", tags=["escalation-audit"])


class EscalationItem(BaseModel):
    id: int
    server_id: str
    server_name: str | None = None
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated_to: str | None = None
    decision_rule_version: str | None = None
    model_version: str
    scored_at: datetime


class EscalationResponse(BaseModel):
    escalations: list[EscalationItem]
    count: int
    total: int
    days: int
    limit: int
    offset: int


def _response_items(result: Any) -> tuple[list[EscalationItem], int]:
    if isinstance(result, tuple) and len(result) == 2:
        raw_items, total = result
    elif isinstance(result, dict):
        raw_items = result.get("escalations", result.get("items", result.get("rows", [])))
        total = result.get("total", len(raw_items))
    else:
        raw_items = result
        total = len(raw_items)

    items = [EscalationItem.model_validate(item) for item in raw_items]
    return items, int(total)


@router.get("/audit/escalations", response_model=EscalationResponse)
def audit_escalations(
    server_id: str | None = Query(default=None),
    axis_name: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> EscalationResponse:
    result = get_escalations(
        db,
        server_id=server_id,
        axis_name=axis_name,
        days=days,
        limit=limit,
        offset=offset,
    )
    items, total = _response_items(result)
    return EscalationResponse(
        escalations=items,
        count=len(items),
        total=total,
        days=days,
        limit=limit,
        offset=offset,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = lambda: object()

    sample_rows = [
        {
            "id": 1,
            "server_id": "server-a",
            "server_name": "Server A",
            "axis_name": "network",
            "label": "high",
            "p_top": 0.91,
            "p_critical": 0.72,
            "p_danger": 0.84,
            "escalated_to": "tier-2",
            "decision_rule_version": "v1",
            "model_version": "model-v1",
            "scored_at": datetime(2025, 1, 1),
        },
        {
            "id": 2,
            "server_id": "server-b",
            "server_name": "Server B",
            "axis_name": "data",
            "label": "critical",
            "p_top": 0.97,
            "p_critical": 0.95,
            "p_danger": 0.93,
            "escalated_to": "tier-3",
            "decision_rule_version": "v1",
            "model_version": "model-v1",
            "scored_at": datetime(2025, 1, 2),
        },
    ]

    original_get_escalations = globals()["get_escalations"]
    globals()["get_escalations"] = lambda *args, **kwargs: (sample_rows, len(sample_rows))
    try:
        with TestClient(local_app) as client:
            response = client.get("/api/audit/escalations")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert payload["count"] == 2
            assert {row["id"] for row in payload["escalations"]} == {1, 2}
    finally:
        globals()["get_escalations"] = original_get_escalations

    print("PASS")