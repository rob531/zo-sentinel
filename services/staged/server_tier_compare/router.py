from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compare_server_tiers


router = APIRouter(prefix="/api", tags=["server-tier-compare"])


class AxisComparison(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    escalated: Optional[bool] = None


class ServerComparison(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    axes: Dict[str, AxisComparison]


class ServerTierComparisonResponse(BaseModel):
    servers: List[ServerComparison]
    axis_order: List[str]
    riskiest_axis_per_server: Dict[str, str]


@router.get(
    "/compare/tiers",
    response_model=ServerTierComparisonResponse,
    summary="Compare LLM axis scores for candidate MCP servers",
)
def compare_tiers(
    server_ids: str = Query(
        ...,
        description="Comma-separated server IDs to compare (2 to 4 IDs)",
    ),
    session: Session = Depends(get_session),
) -> ServerTierComparisonResponse:
    ids = [server_id.strip() for server_id in server_ids.split(",") if server_id.strip()]

    if len(ids) < 2 or len(ids) > 4:
        raise HTTPException(
            status_code=422,
            detail="server_ids must contain between 2 and 4 server IDs",
        )

    return ServerTierComparisonResponse.model_validate(
        compare_server_tiers(session, ids)
    )