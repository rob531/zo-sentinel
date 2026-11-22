"""Router for the advisory severity matrix API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import advisory_severity_matrix

router = APIRouter(prefix="/api", tags=["advisories"])


class SeverityCount(BaseModel):
    severity: str
    count: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int


class SeverityEcosystemCell(BaseModel):
    severity: str
    ecosystem: str
    count: int


class TopLinkedPackage(BaseModel):
    package: str
    severity: str
    linked_servers: int


class SeverityMatrixResponse(BaseModel):
    total_advisories: int
    by_severity: list[SeverityCount]
    by_ecosystem: list[EcosystemCount]
    cells: list[SeverityEcosystemCell]
    top_linked_packages: list[TopLinkedPackage]
    window_days: int


@router.get(
    "/advisories/severity-matrix",
    response_model=SeverityMatrixResponse,
)
def get_advisory_severity_matrix(
    ecosystem: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> SeverityMatrixResponse:
    return advisory_severity_matrix(
        db,
        ecosystem=ecosystem,
        days=days,
    )