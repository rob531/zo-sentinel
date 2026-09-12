# services/staged/daemon_health_status_api/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from app.db import get_session
from .logic import get_daemon_health

router = APIRouter(prefix="/api")


class DaemonStatus(BaseModel):
    name: str
    age_seconds: float
    status: str
    threshold_seconds: float


class DaemonHealthResponse(BaseModel):
    services: List[DaemonStatus]


@router.get("/daemon-health", response_model=DaemonHealthResponse)
async def daemon_health(session=Depends(get_session)):
    return await get_daemon_health(session)