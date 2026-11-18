"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_vuln_link_activity_window

router = APIRouter(prefix="/api/vuln", tags=["vuln_link_activity_window"])


class SeriesItem(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    count: int


class MatchBasisItem(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class ActivityWindowResponse(BaseModel):
    days: int
    series: List[SeriesItem]
    by_match_basis: List[MatchBasisItem]
    total_links: int
    generated_at: datetime


@router.get(
    "/link-activity",
    response_model=ActivityWindowResponse,
    summary="Link activity over the last N days",
)
def get_link_activity(
    days: int = Query(30, ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ActivityWindowResponse:
    """
    Return link activity statistics for the last *days* days.
    """
    data = get_vuln_link_activity_window(db, days)
    return ActivityWindowResponse(**data)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta

    from app.db import Base
    from app.models import VulnLink

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    today = now.date()
    earlier = today - timedelta(days=5)

    link1 = VulnLink(
        advisory_id="ADV-001",
        server_id="SRV-001",
        match_basis="basis1",
        match_value="val1",
        match_confidence=0.9,
        linked_at=datetime.combine(today, datetime.min.time()),
    )
    link2 = VulnLink(
        advisory_id="ADV-002",
        server_id="SRV-002",
        match_basis="basis2",
        match_value="val2",
        match_confidence=0.8,
        linked_at=datetime.combine(today, datetime.min.time()),
    )
    link3 = VulnLink(
        advisory_id="ADV-003",
        server_id="SRV-003",
        match_basis="basis1",
        match_value="val3",
        match_confidence=0.85,
        linked_at=datetime.combine(earlier, datetime.min.time()),
    )
    with SessionLocal() as sess:
        sess.add_all([link1, link2, link3])
        sess.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/vuln/link-activity?days=7")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    assert payload["days"] == 7
    # series must contain today's date with count 2
    today_str = today.isoformat()
    series_dates = {item["date"]: item["count"] for item in payload["series"]}
    assert series_dates.get(today_str) == 2, "Today's count mismatch"
    # by_match_basis must have two entries (basis1 and basis2)
    assert len(payload["by_match_basis"]) == 2, "by_match_basis length mismatch"
    # total_links must be 3
    assert payload["total_links"] == 3, "total_links mismatch"

    print("PASS")