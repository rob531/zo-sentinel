"""HTTP surface for browsing vulnerability advisories."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory as VulnAdvisoryRecord

from .logic import list_vuln_advisories

router = APIRouter(prefix="/api", tags=["vuln_advisory_browse"])


class VulnAdvisory(BaseModel):
    id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str | None
    package: str | None
    source_url: str | None
    published_at: str | None
    fetched_at: str | None
    link_count: int
    affected_ranges: list
    aliases: list


class VulnAdvisoryResponse(BaseModel):
    count: int
    advisories: list[VulnAdvisory]


@router.get("/vulns/advisories", response_model=VulnAdvisoryResponse)
def browse_vuln_advisories(
    severity: str | None = Query(default=None),
    ecosystem: str | None = Query(default=None),
    package: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> VulnAdvisoryResponse:
    rows = list_vuln_advisories(
        db,
        severity=severity,
        ecosystem=ecosystem,
        package=package,
        days=days,
        limit=limit,
    )

    advisories = []
    for row in rows:
        if isinstance(row, dict):
            data = dict(row)
        else:
            data = {
                "id": row.id,
                "feed": row.feed,
                "summary": row.summary,
                "severity": row.severity,
                "ecosystem": row.ecosystem,
                "package": row.package,
                "source_url": row.source_url,
                "published_at": row.published_at,
                "fetched_at": row.fetched_at,
                "link_count": row.link_count,
                "affected_ranges": row.affected_ranges,
                "aliases": row.aliases,
            }
        data["affected_ranges"] = data.get("affected_ranges") or []
        data["aliases"] = data.get("aliases") or []
        advisories.append(VulnAdvisory(**data))

    return VulnAdvisoryResponse(count=len(advisories), advisories=advisories)