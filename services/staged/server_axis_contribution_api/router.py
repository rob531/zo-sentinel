from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import AXIS_NAMES
except ImportError:
    AXIS_NAMES = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )


class OverallRisk(BaseModel):
    p_top: float = 0.0
    p_critical: float = 0.0
    p_danger: float = 0.0
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None


class AxisContribution(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: int
    p_top: float
    weight: int
    contribution: float
    escalated: bool = False
    escalated_to: Optional[str] = None


class ServerAxisContributionResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    overall: OverallRisk
    contributions: List[AxisContribution]
    missing_axes: List[str]
    generated_at: str


router = APIRouter(prefix="/api", tags=["servers"])


def _iso8601(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get(
    "/servers/{server_id}/axis-contribution",
    response_model=ServerAxisContributionResponse,
)
def get_server_axis_contribution(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerAxisContributionResponse:
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    score_rows = session.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.id.desc())
    ).scalars().all()

    axes: dict[str, McpLlmAxisScore] = {}
    for row in score_rows:
        if row.axis_name in AXIS_NAMES and row.axis_name not in axes:
            axes[row.axis_name] = row

    overall_row = axes.get("overall_risk")
    overall = OverallRisk(
        p_top=float(overall_row.p_top or 0.0) if overall_row else 0.0,
        p_critical=float(overall_row.p_critical or 0.0) if overall_row else 0.0,
        p_danger=float(overall_row.p_danger or 0.0) if overall_row else 0.0,
        decision_rule_version=overall_row.decision_rule_version if overall_row else None,
        model_version=overall_row.model_version if overall_row else None,
        scored_at=_iso8601(overall_row.scored_at) if overall_row else None,
    )

    contributions: List[AxisContribution] = []
    for axis_name in AXIS_NAMES:
        if axis_name == "overall_risk":
            continue

        row = axes.get(axis_name)
        if row is None:
            continue

        p_top = float(row.p_top or 0.0)
        weight = int(row.label_index)
        contributions.append(
            AxisContribution(
                axis_name=axis_name,
                label=row.label,
                label_index=weight,
                p_top=p_top,
                weight=weight,
                contribution=p_top * weight,
                escalated=bool(row.escalated),
                escalated_to=row.escalated_to,
            )
        )

    contributions.sort(key=lambda item: item.contribution, reverse=True)
    missing_axes = [axis_name for axis_name in AXIS_NAMES if axis_name not in axes]

    return ServerAxisContributionResponse(
        server_id=server_id,
        name=server.name,
        risk_tier=server.risk_tier,
        overall=overall,
        contributions=contributions,
        missing_axes=missing_axes,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    print("PASS")