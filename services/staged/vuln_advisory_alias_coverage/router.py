"""HTTP surface for vulnerability advisory alias coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .logic import compute_alias_coverage

router = APIRouter(prefix="/api/vuln", tags=["vuln"])


class AliasCoverageResponse(BaseModel):
    coverage: dict[str, Any]
    generated_at: str


@router.get("/alias-coverage", response_model=AliasCoverageResponse)
def get_alias_coverage(db: Session = Depends(get_session)) -> AliasCoverageResponse:
    advisory_rows = db.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.aliases,
            VulnAdvisory.published_at,
        )
    ).all()
    link_rows = db.execute(
        select(
            VulnLink.id,
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_confidence,
        )
    ).all()

    advisories = [
        {
            "id": row.id,
            "feed": row.feed,
            "severity": row.severity,
            "ecosystem": row.ecosystem,
            "package": row.package,
            "aliases": row.aliases,
            "published_at": row.published_at,
        }
        for row in advisory_rows
    ]
    links = [
        {
            "id": row.id,
            "advisory_id": row.advisory_id,
            "server_id": row.server_id,
            "match_basis": row.match_basis,
            "match_confidence": row.match_confidence,
        }
        for row in link_rows
    ]

    coverage = compute_alias_coverage(advisories, links)
    return AliasCoverageResponse(
        coverage=coverage,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )