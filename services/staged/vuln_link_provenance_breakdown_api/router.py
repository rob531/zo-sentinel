from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import get_vuln_link_provenance_breakdown


router = APIRouter(prefix="/api", tags=["vulnerability"])


class SeverityBreakdown(BaseModel):
    severity: str
    count: int


class ProvenanceGroup(BaseModel):
    match_basis: str
    links: int
    distinct_servers: int
    distinct_advisories: int
    avg_confidence: float
    min_confidence: float
    max_confidence: float
    by_severity: dict[str, int] = Field(default_factory=dict)


class ProvenanceBreakdownResponse(BaseModel):
    bases: list[ProvenanceGroup] = Field(default_factory=list)


@router.get(
    "/vuln-links/provenance",
    response_model=ProvenanceBreakdownResponse,
)
def get_vuln_links_provenance(
    severity: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> ProvenanceBreakdownResponse:
    return get_vuln_link_provenance_breakdown(
        session,
        severity=severity,
        min_confidence=min_confidence,
    )