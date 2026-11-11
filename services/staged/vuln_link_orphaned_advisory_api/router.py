from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import get_orphaned_links, get_orphaned_links_summary


router = APIRouter(prefix="/api", tags=["vuln-links"])


class MatchBasisSummary(BaseModel):
    match_basis: str
    count: int
    mean_match_confidence: float


class OrphanedLink(BaseModel):
    id: int
    advisory_id: str
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: str


class OrphanedLinksResponse(BaseModel):
    total: int
    distinct_servers: int
    by_match_basis: list[MatchBasisSummary]
    links: list[OrphanedLink]
    generated_at: str


class OrphanedLinksSummaryResponse(BaseModel):
    total: int
    distinct_servers: int
    by_match_basis: list[MatchBasisSummary]
    oldest_linked_at: str | None
    newest_linked_at: str | None


@router.get(
    "/vuln/links/orphans",
    response_model=OrphanedLinksResponse,
)
def list_orphaned_links(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session=Depends(get_session),
):
    return get_orphaned_links(session, limit=limit, offset=offset)


@router.get(
    "/vuln/links/orphans/summary",
    response_model=OrphanedLinksSummaryResponse,
)
def summarize_orphaned_links(session=Depends(get_session)):
    return get_orphaned_links_summary(session)