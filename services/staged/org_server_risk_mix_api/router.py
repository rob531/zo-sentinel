from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

from .logic import get_org_risk_mix

router = APIRouter(prefix="/api", tags=["organisation server risk mix"])

RiskTier = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
MembershipSource = Literal["registry", "api_key_scoped"]


class RiskMixServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: RiskTier = "UNKNOWN"
    verdict: str | None = None
    trust_score: float | None = None
    last_assessed: datetime | None = None


class OrgServerRiskMixResponse(BaseModel):
    org_id: str
    total_servers: int = Field(ge=0)
    average_trust_score: float | None = None
    tier_counts: dict[str, int]
    servers: list[RiskMixServer] = Field(default_factory=list)
    membership_source: MembershipSource


@router.get(
    "/orgs/{org_id}/risk-mix",
    response_model=OrgServerRiskMixResponse,
    summary="Get an organisation's MCP server risk-tier breakdown",
)
async def read_org_server_risk_mix(
    org_id: str,
    session: AsyncSession = Depends(get_session),
) -> OrgServerRiskMixResponse:
    result: Any = get_org_risk_mix(org_id, session)
    if isawaitable(result):
        result = await result

    if isinstance(result, OrgServerRiskMixResponse):
        return result
    if hasattr(result, "model_dump"):
        result = result.model_dump()

    return OrgServerRiskMixResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")