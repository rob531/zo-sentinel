"""
Scorecard Badge Router - Thin APIRouter exposing scorecard for a server.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Literal
from app.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scorecard"])


RISK_TIER_COLORS = {
    "TRUSTED_GENERAL": "#22c55e",
    "TRUSTED_RESEARCH": "#84cc16",
    "ENTERPRISE_CONTROLLED": "#eab308",
    "CAUTION_LIMITED": "#f97316",
    "HIGH_RISK_ISOLATED": "#ef4444",
    "KNOWN_THREAT": "#000000",
    "INSUFFICIENT": "#6b7280",
}

AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisScoreResponse(BaseModel):
    axis: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class OverallBadgeResponse(BaseModel):
    risk_tier: str
    color: str
    name: str
    verdict: str


class ScorecardBadgeResponse(BaseModel):
    server_id: str
    axes: list[AxisScoreResponse]
    overall: OverallBadgeResponse


@router.get("/servers/{server_id}/scorecard", response_model=ScorecardBadgeResponse)
async def get_scorecard_badge(
    server_id: str,
    session: AsyncSession = Depends(get_session),
) -> ScorecardBadgeResponse:
    """
    Get scorecard badge for a server.
    Returns per-axis scores and overall composite badge.
    """
    # Fetch all axis scores for this server
    result = await session.execute(
        select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    )
    axis_scores = result.scalars().all()

    # Fetch server registry entry
    reg_result = await session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    )
    server = reg_result.scalar_one_or_none()

    if server is None:
        # Return empty response for unknown server
        return ScorecardBadgeResponse(
            server_id=server_id,
            axes=[],
            overall=OverallBadgeResponse(
                risk_tier="INSUFFICIENT",
                color=RISK_TIER_COLORS["INSUFFICIENT"],
                name="Unknown",
                verdict="unknown",
            ),
        )

    # Build axes list
    axes_dict = {score.axis: score for score in axis_scores}
    axes = []
    for axis_name in AXIS_NAMES:
        if axis_name in axes_dict:
            score = axes_dict[axis_name]
            axes.append(
                AxisScoreResponse(
                    axis=axis_name,
                    label=axis_name.replace("_", " ").title(),
                    p_top=float(score.p_top or 0.0),
                    p_critical=float(score.p_critical or 0.0),
                    p_danger=float(score.p_danger or 0.0),
                    escalated=bool(score.escalated),
                )
            )

    # Build overall badge
    risk_tier = server.risk_tier or "INSUFFICIENT"
    color = RISK_TIER_COLORS.get(risk_tier, RISK_TIER_COLORS["INSUFFICIENT"])

    overall = OverallBadgeResponse(
        risk_tier=risk_tier,
        color=color,
        name=server.name or "Unknown",
        verdict=server.verdict or "unknown",
    )

    return ScorecardBadgeResponse(
        server_id=server_id,
        axes=axes,
        overall=overall,
    )


# Export for use by other modules
async def get_server_axis_scores(
    server_id: str,
    session: AsyncSession,
) -> list[AxisScoreResponse]:
    """Get axis scores for a server."""
    result = await session.execute(
        select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    )
    axis_scores = result.scalars().all()
    axes = []
    for axis_name in AXIS_NAMES:
        for score in axis_scores:
            if score.axis == axis_name:
                axes.append(
                    AxisScoreResponse(
                        axis=axis_name,
                        label=axis_name.replace("_", " ").title(),
                        p_top=float(score.p_top or 0.0),
                        p_critical=float(score.p_critical or 0.0),
                        p_danger=float(score.p_danger or 0.0),
                        escalated=bool(score.escalated),
                    )
                )
                break
    return axes


async def risk_tier_by_id(
    server_id: str,
    session: AsyncSession,
) -> str:
    """Get risk tier for a server by ID."""
    result = await session.execute(
        select(McpServerRegistry.risk_tier).where(
            McpServerRegistry.server_id == server_id
        )
    )
    tier = result.scalar_one_or_none()
    return tier or "INSUFFICIENT"


async def get_verdict_summary(
    server_id: str,
    session: AsyncSession,
) -> str:
    """Get verdict for a server."""
    result = await session.execute(
        select(McpServerRegistry.verdict).where(
            McpServerRegistry.server_id == server_id
        )
    )
    verdict = result.scalar_one_or_none()
    return verdict or "unknown"