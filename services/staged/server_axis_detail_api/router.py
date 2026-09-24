from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_axis_detail_api"])


class AxisResponse(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class ServerAxisDetailResponse(BaseModel):
    server_id: str
    risk_tier: str
    axes: list[AxisResponse]


def get_server_axis_detail(session: Session, server_id: str) -> dict:
    from .logic import fetch_axis_scores, fetch_server_risk_tier
    risk_tier = fetch_server_risk_tier(session, server_id)
    axes = fetch_axis_scores(session, server_id)
    return {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "axes": axes
    }


@router.get("/servers/{server_id}/axes", response_model=ServerAxisDetailResponse)
def get_server_axes(
    server_id: str,
    session: Session = Depends(get_session)
) -> ServerAxisDetailResponse:
    result = get_server_axis_detail(session, server_id)
    if result["risk_tier"] is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return ServerAxisDetailResponse(**result)