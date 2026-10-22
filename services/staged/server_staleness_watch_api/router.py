from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from .logic import get_staleness


router = APIRouter(prefix="/api", tags=["servers"])


class StaleServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    scan_count: int = 0
    days_since_scan: Optional[int] = None


class StalenessResponse(BaseModel):
    days: int
    stale: List[StaleServer]
    count: int
    by_registry_source: Dict[str, int]
    by_risk_tier: Dict[str, int]


@router.get("/servers/staleness", response_model=StalenessResponse)
def server_staleness(
    days: int = Query(default=7, ge=1),
    session=Depends(get_session),
):
    return get_staleness(session, days)