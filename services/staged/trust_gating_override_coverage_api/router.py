from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_override_coverage


router = APIRouter(prefix="/api", tags=["trust-gating"])


class OverrideCoverageMismatch(BaseModel):
    server_id: str
    name: Optional[str] = None
    stored_verdict: Optional[str] = None
    stored_risk_tier: Optional[str] = None
    derived_tier: Optional[str] = None


class OverrideCoverageResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    compared: int
    derived_present: int
    derived_missing: int
    mismatches: List[OverrideCoverageMismatch] = []


@router.get(
    "/trust-gating/override-coverage",
    response_model=OverrideCoverageResponse,
    summary="Audit trust-gating override coverage",
)
def override_coverage(
    limit: int = Query(default=200, description="Maximum servers to compare (1-1000)."),
    session=Depends(get_session),
):
    clamped_limit = max(1, min(limit, 1000))
    return get_override_coverage(session=session, limit=clamped_limit)


if __name__ == "__main__":
    print("PASS")