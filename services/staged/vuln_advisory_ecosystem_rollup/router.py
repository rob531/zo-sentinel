"""HTTP surface for vulnerability advisory ecosystem rollups."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import ecosystem_rollup

router = APIRouter(prefix="/api", tags=["vuln"])


class SeverityBreakdown(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    unknown: int


class EcosystemRollupEntry(BaseModel):
    ecosystem: str
    count: int
    by_severity: SeverityBreakdown


class EcosystemRollupResponse(BaseModel):
    total: int
    by_ecosystem: list[EcosystemRollupEntry]
    newest_published_at: str | None
    avg_fetch_lag_hours: float | None


@router.get(
    "/vuln/advisories/ecosystem-rollup",
    response_model=EcosystemRollupResponse,
)
def get_ecosystem_rollup(
    db: Session = Depends(get_session),
) -> EcosystemRollupResponse:
    return EcosystemRollupResponse(**ecosystem_rollup(db))