from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import get_axis_escalation_routing

router = APIRouter(prefix="/api", tags=["scoring"])


class AxisEscalationRoutingRow(BaseModel):
    axis_name: str
    escalated_to: Optional[str]
    label: Optional[str]
    count: int
    escalated: int


class AxisEscalationRoutingTotal(BaseModel):
    axis_name: str
    count: int
    escalated: int


class AxisEscalationRoutingResponse(BaseModel):
    days: int
    rows: List[AxisEscalationRoutingRow]
    totals: List[AxisEscalationRoutingTotal]


def _normalise_rows(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("rows", [])
    return [dict(item) for item in value]


def _normalise_totals(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("totals", [])
    return [dict(item) for item in value]


@router.get(
    "/scoring/axis-escalation-routing",
    response_model=AxisEscalationRoutingResponse,
)
def get_axis_escalation_routing_route(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
) -> AxisEscalationRoutingResponse:
    payload = get_axis_escalation_routing(session, days)

    if isinstance(payload, dict):
        raw_rows = payload.get("rows", [])
        raw_totals = payload.get("totals", [])
    elif isinstance(payload, (tuple, list)) and len(payload) == 2:
        raw_rows, raw_totals = payload
    else:
        raw_rows = payload
        raw_totals = []

    rows = [
        AxisEscalationRoutingRow(
            axis_name=str(item["axis_name"]),
            escalated_to=(
                str(item["escalated_to"])
                if item.get("escalated_to") is not None
                else None
            ),
            label=str(item["label"]) if item.get("label") is not None else None,
            count=int(item["count"]),
            escalated=int(item.get("escalated", 0)),
        )
        for item in _normalise_rows(raw_rows)
    ]
    rows.sort(key=lambda item: (-item.count, item.axis_name))

    totals = [
        AxisEscalationRoutingTotal(
            axis_name=str(item["axis_name"]),
            count=int(item["count"]),
            escalated=int(item.get("escalated", 0)),
        )
        for item in _normalise_totals(raw_totals)
    ]
    totals.sort(key=lambda item: (-item.count, item.axis_name))

    if not totals:
        totals_by_axis: Dict[str, AxisEscalationRoutingTotal] = {}
        for row in rows:
            total = totals_by_axis.setdefault(
                row.axis_name,
                AxisEscalationRoutingTotal(
                    axis_name=row.axis_name,
                    count=0,
                    escalated=0,
                ),
            )
            total.count += row.count
            total.escalated += row.escalated
        totals = sorted(
            totals_by_axis.values(),
            key=lambda item: (-item.count, item.axis_name),
        )

    return AxisEscalationRoutingResponse(
        days=days,
        rows=rows,
        totals=totals,
    )