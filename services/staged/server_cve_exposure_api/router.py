from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, VulnAdvisory, VulnLink
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime

router = APIRouter(prefix="/api", tags=["exemplar"])


class AxisScoreResponse(BaseModel):
    axis_name: str
    scored_at: datetime
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None

    class Config:
        from_attributes = True


@router.get("/exemplar/scores/{server_id}")
def get_axis_scores(
    server_id: str,
    session: Session = Depends(get_session)
) -> List[AxisScoreResponse]:
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    return scores


@router.get("/health")
def get_daemon_health():
    return {"status": "healthy"}