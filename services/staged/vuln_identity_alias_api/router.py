"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import resolve_vuln_identifier

router = APIRouter(prefix="/api/vuln_identity_alias", tags=["vuln_identity_alias"])


class VulnAdvisoryResponse(BaseModel):
    id: int
    feed: str
    source_url: str
    ecosystem: str | None
    package: str | None
    severity: str | None
    summary: str | None
    published_at: str | None
    fetched_at: str
    content_hash: str | None
    identities: list[str] | None
    aliases: list[str] | None
    affected_ranges: list[str] | None


@router.get("/resolve/{identifier}", response_model=list[VulnAdvisoryResponse])
def resolve_vuln_identifier_endpoint(
    identifier: str, db: Session = Depends(get_session)
) -> list[VulnAdvisoryResponse]:
    advisories = resolve_vuln_identifier(db, identifier)
    return [
        VulnAdvisoryResponse(
            id=advisory.id,
            feed=advisory.feed,
            source_url=advisory.source_url,
            ecosystem=advisory.ecosystem,
            package=advisory.package,
            severity=advisory.severity,
            summary=advisory.summary,
            published_at=advisory.published_at,
            fetched_at=advisory.fetched_at,
            content_hash=advisory.content_hash,
            identities=advisory.identities,
            aliases=advisory.aliases,
            affected_ranges=advisory.affected_ranges,
        )
        for advisory in advisories
    ]