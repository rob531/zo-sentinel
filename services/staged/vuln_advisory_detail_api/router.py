from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_advisory_detail

router = APIRouter()


class AdvisoryResponse(BaseModel):
    id: str
    feed: str | None = None
    summary: str | None = None
    severity: str | None = None
    ecosystem: str | None = None
    package: str | None = None
    affected_ranges: list | None = None
    aliases: list | None = None
    source_url: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    identities: list | None = None
    content_hash: str | None = None


class AffectedServerResponse(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    match_basis: str | None = None
    match_value: str | None = None
    match_confidence: float | None = None
    linked_at: str | None = None


class AdvisoryDetailResponse(BaseModel):
    advisory: AdvisoryResponse
    affected_servers: list[AffectedServerResponse]
    affected_server_count: int


@router.get(
    "/vuln/advisory/{advisory_id}",
    response_model=AdvisoryDetailResponse,
    responses={404: {"description": "Advisory not found"}},
)
def read_advisory_detail(
    advisory_id: str,
    session: Session = Depends(get_session),
) -> AdvisoryDetailResponse:
    detail = get_advisory_detail(advisory_id, session)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "advisory_not_found", "advisory_id": advisory_id},
        )
    return detail