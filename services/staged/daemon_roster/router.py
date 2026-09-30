# services/staged/daemon_roster/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional

from app.db import get_session

from .logic import get_daemon_health

router = APIRouter(prefix="/api")


class DaemonMeta(BaseModel):
    name: str
    status: str
    last_heartbeat: Optional[str] = None
    meta: Optional[dict] = None


class DaemonHealthResponse(BaseModel):
    daemons: List[DaemonMeta]
    stale_count: int
    healthy_count: int


@router.get("/health/daemons", response_model=DaemonHealthResponse)
async def health_daemons(session=Depends(get_session)):
    return await get_daemon_health(session)