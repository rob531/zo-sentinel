from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_surface_lag

router = APIRouter(prefix="/api")


class SurfaceLagItem(BaseModel):
    surface: str
    last_computed_at: datetime
    age_seconds: float
    compute_seconds: float
    payload_keys: List[str]


class SurfaceLagResponse(BaseModel):
    generated_at: datetime
    surfaces: List[SurfaceLagItem]
    stale_count: int


@router.get("/freshness/surface-lag", response_model=SurfaceLagResponse)
def get_freshness_surface_lag(
    session: Session = Depends(get_session),
) -> SurfaceLagResponse:
    return get_surface_lag(session)