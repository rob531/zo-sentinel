"""MIRROR services/_exemplar/router.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import List, Optional
from .logic import get_servers_comparison

router = APIRouter(prefix="/api", tags=["server_risk_comparison"])

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]

class ServerComparison(BaseModel):
    server_id: str
    name: str
    axes: List[AxisScore]
    overall_risk: Optional[float]
    risk_tier: Optional[str]

class ComparisonResponse(BaseModel):
    servers: List[ServerComparison]

@router.get("/servers/compare", response_model=ComparisonResponse)
def compare_servers(server_ids: str, session: Session = Depends(get_session)):
    """
    Compare risk axes across multiple servers.
    Query param: server_ids=id1,id2,...
    Returns axis scores for each server.
    """
    id_list = [s.strip() for s in server_ids.split(",") if s.strip()]
    return get_servers_comparison(session, id_list)