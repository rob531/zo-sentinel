"""HTTP router for vulnerability advisory digests."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_advisory, list_advisories

router = APIRouter(prefix="/api", tags=["vuln-advisory-digest"])


@router.get("/vuln/advisories")
def get_vuln_advisories(
    severity: str | None = Query(default=None),
    ecosystem: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return list_advisories(
        db,
        severity=severity,
        ecosystem=ecosystem,
        limit=limit,
        offset=offset,
    )


@router.get("/vuln/advisories/{advisory_id}")
def get_vuln_advisory(
    advisory_id: str,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_advisory(db, advisory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Advisory not found")
    return result