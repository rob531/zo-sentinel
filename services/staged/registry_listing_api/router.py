from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import list_servers

router = APIRouter(prefix="/api", tags=["registry"])


class ServerItem(BaseModel):
    server_id: str
    name: str
    registry_source: Optional[str]
    trust_score: Optional[float]
    verdict: Optional[str]
    confidence: Optional[float]
    risk_tier: Optional[str]
    last_assessed: Optional[datetime]
    last_seen: Optional[datetime]
    scan_count: int


class ServerListResponse(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    servers: List[ServerItem]


@router.get("/registry/servers", response_model=ServerListResponse)
def list_registry_servers(
    risk_tier: Optional[str] = Query(default=None),
    registry_source: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ServerListResponse:
    result = list_servers(
        session,
        risk_tier=risk_tier,
        registry_source=registry_source,
        q=q,
        limit=limit,
        offset=offset,
    )
    return ServerListResponse(**result)