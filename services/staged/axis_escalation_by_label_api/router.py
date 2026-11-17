from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_escalation_by_label

try:
    from .contract import (
        AxisEscalationByLabelResponse,
        AxisEscalationGroup,
    )
except ImportError:
    class AxisEscalationGroup(BaseModel):
        axis_name: str
        label: str
        label_index: int
        total: int
        escalated_count: int
        escalation_ratio: float

    class AxisEscalationByLabelResponse(BaseModel):
        days: int
        groups: list[AxisEscalationGroup]


router = APIRouter(prefix="/api", tags=["axis_escalation_by_label"])


def _group_value(group: Any, name: str) -> Any:
    if isinstance(group, dict):
        return group.get(name)
    return getattr(group, name)


@router.get(
    "/axis/escalation-by-label",
    response_model=AxisEscalationByLabelResponse,
)
def axis_escalation_by_label(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
) -> AxisEscalationByLabelResponse:
    result = get_escalation_by_label(session, days)

    if isinstance(result, dict):
        groups = result.get("groups", [])
    elif hasattr(result, "groups"):
        groups = result.groups
    else:
        groups = result or []

    response_groups = [
        AxisEscalationGroup(
            axis_name=_group_value(group, "axis_name"),
            label=_group_value(group, "label"),
            label_index=_group_value(group, "label_index"),
            total=_group_value(group, "total"),
            escalated_count=_group_value(group, "escalated_count"),
            escalation_ratio=_group_value(group, "escalation_ratio"),
        )
        for group in groups
    ]

    return AxisEscalationByLabelResponse(days=days, groups=response_groups)


if __name__ == "__main__":
    sample = AxisEscalationByLabelResponse(
        days=30,
        groups=[
            AxisEscalationGroup(
                axis_name="security",
                label="critical",
                label_index=0,
                total=3,
                escalated_count=1,
                escalation_ratio=1 / 3,
            )
        ],
    )
    assert sample.days == 30
    assert sample.groups[0].escalated_count == 1
    print("PASS")