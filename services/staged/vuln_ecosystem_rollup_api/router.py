from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import get_ecosystem_rollup

router = APIRouter(prefix="/api", tags=["vuln-ecosystem-rollup"])


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class EcosystemRollup(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_servers: int
    severity_counts: SeverityCounts
    avg_match_confidence: float | None = None


class EcosystemRollupResponse(BaseModel):
    total_advisories: int
    ecosystems: list[EcosystemRollup]
    generated_at: datetime


@router.get(
    "/vuln/ecosystem-rollup",
    response_model=EcosystemRollupResponse,
    summary="Vulnerability advisory rollup by ecosystem",
)
def ecosystem_rollup(
    session: Session = Depends(get_session),
) -> EcosystemRollupResponse:
    payload: dict[str, Any] = get_ecosystem_rollup(session)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    return EcosystemRollupResponse(**payload)


__all__ = [
    "EcosystemRollup",
    "EcosystemRollupResponse",
    "SeverityCounts",
    "router",
]