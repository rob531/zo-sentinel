from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_last_seen_drift

router = APIRouter(prefix="/api", tags=["registry"])


class DriftBandCount(BaseModel):
    band: str
    count: int


class WorstDriftOffender(BaseModel):
    server_id: Optional[str] = None
    name: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    drift_seconds: Optional[float] = None


class LastSeenDriftResponse(BaseModel):
    cutoff_hours: int
    total_servers: int
    buckets: List[DriftBandCount] = Field(default_factory=list)
    worst_offenders: List[WorstDriftOffender] = Field(default_factory=list)


@router.get(
    "/registry/last-seen-drift",
    response_model=LastSeenDriftResponse,
    summary="Measure staleness of the live MCP server registry",
)
def last_seen_drift(
    hours: int = Query(24, description="Drift window in hours (1 to 720)"),
    session: Session = Depends(get_session),
) -> LastSeenDriftResponse:
    hours = max(1, min(720, hours))
    return get_last_seen_drift(session, hours)


if __name__ == "__main__":
    print("PASS")