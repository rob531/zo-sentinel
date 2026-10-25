from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scan_throughput

router = APIRouter(prefix="/api", tags=["scan-throughput"])


class ScanThroughputLane(BaseModel):
    registry_source: str
    servers: int
    scans: int


class ScanThroughputResponse(BaseModel):
    hours: int
    servers_seen: int
    servers_scanned: int
    total_scan_count: int
    lanes: list[ScanThroughputLane]
    median_scan_count: float
    generated_at: datetime


@router.get("/servers/scan-throughput", response_model=ScanThroughputResponse)
def scan_throughput(
    hours: int = 24,
    session: Session = Depends(get_session),
) -> ScanThroughputResponse:
    return get_scan_throughput(session=session, hours=hours)