from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_vuln_link_confidence as _get_confidence_report
except ImportError:
    try:
        from .logic import get_vuln_link_confidence_report as _get_confidence_report
    except ImportError:
        try:
            from .logic import get_confidence_report as _get_confidence_report
        except ImportError:
            from .logic import get_vuln_link_confidence_data as _get_confidence_report


router = APIRouter(prefix="/api")


class MatchBasisSummary(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class SeveritySummary(BaseModel):
    severity: str
    count: int


class LowConfidenceLink(BaseModel):
    link_id: int
    server_id: str
    advisory_id: int
    match_value: str
    match_confidence: float


class VulnLinkConfidenceResponse(BaseModel):
    total_links: int
    by_basis: List[MatchBasisSummary]
    by_severity: List[SeveritySummary]
    low_confidence_count: int
    low_confidence_threshold: float = 0.5
    top_low_confidence: List[LowConfidenceLink]


@router.get(
    "/vuln/links/confidence",
    response_model=VulnLinkConfidenceResponse,
)
def get_vuln_link_confidence(
    session: Session = Depends(get_session),
) -> VulnLinkConfidenceResponse:
    return _get_confidence_report(session)