from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

from .logic import get_scan_count_bands

router = APIRouter(prefix="/api", tags=["registry"])


class ScanCountBand(BaseModel):
    band: str
    lower: int
    upper: Optional[int]
    server_count: int
    pct: float


class ScanCountBandsResponse(BaseModel):
    total_servers: int
    never_scanned: int
    bands: list[ScanCountBand]
    median_scan_count: float


@router.get(
    "/registry/scan-count-bands",
    response_model=ScanCountBandsResponse,
)
def scan_count_bands(session=Depends(get_session)) -> ScanCountBandsResponse:
    payload = get_scan_count_bands(session)
    return ScanCountBandsResponse(**payload)