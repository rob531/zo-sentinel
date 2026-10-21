from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_freshness

router = APIRouter(prefix="/api")


class RegistryScanFreshnessSource(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    scanned_24h: int
    scanned_7d: int
    scanned_30d: int
    stale_over_30d: int
    median_days_since_scan: float | None


class RegistryScanFreshnessResponse(BaseModel):
    total_servers: int
    never_scanned: int
    sources: list[RegistryScanFreshnessSource]


@router.get(
    "/registry/scan-freshness",
    response_model=RegistryScanFreshnessResponse,
)
def registry_scan_freshness(
    session: Session = Depends(get_session),
) -> RegistryScanFreshnessResponse:
    report = get_scan_freshness(session)
    return RegistryScanFreshnessResponse(**report)