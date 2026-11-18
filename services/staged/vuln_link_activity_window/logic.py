"""Logic for reporting CVE-to-server link activity over a recent window."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


def _as_utc_date(value: datetime) -> date:
    """Return the UTC calendar date for a database timestamp."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.date()


def link_activity_window(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return daily link counts and match-basis statistics for recent links."""
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    today = datetime.now(timezone.utc).date()
    series_dates = [
        today - timedelta(days=offset)
        for offset in range(days - 1, -1, -1)
    ] if days else []

    rows: List[Any] = []
    if days:
        cutoff = datetime.combine(series_dates[0], time.min)
        rows = db.execute(
            select(
                VulnLink.linked_at,
                VulnLink.match_basis,
                VulnLink.match_confidence,
            )
            .where(VulnLink.linked_at >= cutoff)
            .order_by(VulnLink.linked_at)
        ).all()

    counts = {day: 0 for day in series_dates}
    basis_counts: Dict[str, int] = {}
    confidence_totals: Dict[str, float] = {}

    for linked_at, match_basis, match_confidence in rows:
        link_date = _as_utc_date(linked_at)
        if link_date in counts:
            counts[link_date] += 1

        basis = match_basis or "unknown"
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        confidence_totals[basis] = confidence_totals.get(basis, 0.0) + float(
            match_confidence or 0.0
        )

    series = [
        {"date": day.isoformat(), "count": counts[day]}
        for day in series_dates
    ]
    by_match_basis = [
        {
            "match_basis": basis,
            "count": basis_counts[basis],
            "avg_confidence": confidence_totals[basis] / basis_counts[basis],
        }
        for basis in sorted(basis_counts)
    ]

    return {
        "days": days,
        "series": series,
        "by_match_basis": by_match_basis,
        "total_links": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Keep descriptive aliases available to routers and direct contract callers.
get_link_activity_window = link_activity_window
get_vuln_link_activity_window = link_activity_window
vuln_link_activity_window = link_activity_window
get_vuln_link_activity = link_activity_window


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = now - timedelta(days=1)
    with SessionLocal() as session:
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id="CVE-2024-0001",
                    server_id="server-1",
                    match_basis="cpe",
                    match_value="cpe:/a:example:one",
                    match_confidence=0.95,
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id="CVE-2024-0002",
                    server_id="server-2",
                    match_basis="hostname",
                    match_value="host.example",
                    match_confidence=0.85,
                    linked_at=now,
                ),
                VulnLink(
                    id=3,
                    advisory_id="CVE-2024-0003",
                    server_id="server-3",
                    match_basis="cpe",
                    match_value="cpe:/a:example:two",
                    match_confidence=0.75,
                    linked_at=yesterday,
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/test")
    def test_endpoint(db: Session = Depends(get_session)):
        return link_activity_window(db, days=30)

    response = TestClient(local_app).get("/test")
    payload = response.json()
    assert response.status_code == 200
    assert payload["days"] == 30
    assert any(
        item["date"] == now.date().isoformat() and item["count"] == 2
        for item in payload["series"]
    )
    assert len(payload["by_match_basis"]) == 2
    assert payload["total_links"] == 3
    print("PASS")