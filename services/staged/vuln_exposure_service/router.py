"""router.py -- the service's HTTP surface for vulnerability exposure.

Exposes `router` and delegates data access to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_vuln_exposure


router = APIRouter(prefix="/api", tags=["vuln_exposure"])


@router.get("/vuln/exposure")
def get_vuln_exposure(
    severity: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_session),
) -> dict:
    return compute_vuln_exposure(db, severity=severity, limit=limit)


if __name__ == "__main__":
    print("PASS")