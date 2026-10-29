from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_gap

router = APIRouter(prefix="/api", tags=["registry"])


class ScanGapBand(BaseModel):
    band: str
    count: int
    pct: float


class ScanGapOffender(BaseModel):
    server_id: str
    name: str
    registry_source: str | None = None
    risk_tier: str | None = None
    days_since_scan: int


class ScanFreshnessGapResponse(BaseModel):
    total_servers: int
    scanned_never: int
    bands: List[ScanGapBand]
    worst_offenders: List[ScanGapOffender]


@router.get("/registry/scan-gap", response_model=ScanFreshnessGapResponse)
def scan_freshness_gap(
    session: Session = Depends(get_session),
) -> ScanFreshnessGapResponse:
    return get_scan_gap(session)


if __name__ == "__main__":
    assert any(
        route.path == "/api/registry/scan-gap" for route in router.routes
    )
    print("PASS")