"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the service include/mount point. Thin: injects the
real Session via Depends(get_session) and delegates to logic.py.
Intra-service imports are relative so staged and active deployments behave
identically.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_slo

router = APIRouter(prefix="/api", tags=["freshness_slo_api"])


@router.get("/freshness/slo")
def freshness_slo(
    db: Session = Depends(get_session),
    reference_time: datetime | None = Query(default=None),
) -> dict[str, Any]:
    """Return freshness information for all known surfaces."""
    return get_freshness_slo(db, reference_time=reference_time)


@router.get("/freshness/slo/{surface}")
def freshness_slo_surface(
    surface: str,
    db: Session = Depends(get_session),
    reference_time: datetime | None = Query(default=None),
) -> dict[str, Any]:
    """Return freshness information for one known surface."""
    result = get_freshness_slo(db, reference_time=reference_time)
    for item in result.get("surfaces", []):
        if item.get("surface") == surface:
            return item
    raise HTTPException(status_code=404, detail=f"Surface not found: {surface}")


if __name__ == "__main__":
    print("PASS")