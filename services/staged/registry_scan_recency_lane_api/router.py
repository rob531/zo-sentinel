from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ....db.session import get_session
from .logic import get_registry_scan_recency

router = APIRouter(prefix="/api", tags=["registry-scan-recency"])


class RegistryScanRecencyLane(BaseModel):
    source: str
    total: int
    never_scanned: int
    scanned_7d: int
    stale_30d: int
    avg_scan_count: float


class RegistryScanRecencyTotals(BaseModel):
    servers: int
    never_scanned: int


class RegistryScanRecencyResponse(BaseModel):
    generated_at: str
    lanes: List[RegistryScanRecencyLane]
    totals: RegistryScanRecencyTotals


@router.get(
    "/registry/scan-recency",
    response_model=RegistryScanRecencyResponse,
)
def registry_scan_recency(
    source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> RegistryScanRecencyResponse:
    return get_registry_scan_recency(session, source)