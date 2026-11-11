from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_advisory as _get_advisory
from .logic import list_advisories as _list_advisories


router = APIRouter(prefix="/api")


@router.get("/vulns/advisories")
def list_advisories(
    ecosystem: str | None = None,
    severity: str | None = None,
    package: str | None = None,
    days: int | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    return _list_advisories(
        ecosystem=ecosystem,
        severity=severity,
        package=package,
        days=days,
        limit=limit,
        session=session,
    )


@router.get("/vulns/advisories/{advisory_id}")
def get_advisory(
    advisory_id: int,
    session: Session = Depends(get_session),
):
    return _get_advisory(advisory_id=advisory_id, session=session)


if __name__ == "__main__":
    paths = {route.path for route in router.routes}
    assert "/api/vulns/advisories" in paths
    assert "/api/vulns/advisories/{advisory_id}" in paths
    print("PASS")