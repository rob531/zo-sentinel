from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_confidence_audit

router = APIRouter(prefix="/api", tags=["vulnerability-link-confidence"])


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int
    share: float


class LowConfidenceLink(BaseModel):
    advisory_id: str
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float
    severity: str
    linked_at: datetime


class VulnLinkConfidenceResponse(BaseModel):
    total_links: int
    buckets: List[ConfidenceBucket]
    low_confidence: List[LowConfidenceLink]
    generated_at: datetime


@router.get(
    "/vuln/links/confidence",
    response_model=VulnLinkConfidenceResponse,
)
def vuln_link_confidence_audit(
    session: Session = Depends(get_session),
) -> VulnLinkConfidenceResponse:
    return get_confidence_audit(session)