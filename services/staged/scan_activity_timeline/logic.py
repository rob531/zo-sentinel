"""logic.py -- scan activity timeline computation over the real registry."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def scan_activity_timeline(db: Session, days: int = 14) -> Dict[str, Any]:
    """Return daily scan activity and registry-source totals for the window."""
    window_days = max(1, min(int(days), 90))
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=window_days - 1)

    scan_date = func.date(McpServerRegistry.last_scanned)
    window_filter = (scan_date >= first_day) & (scan_date <= today)

    daily_rows = db.execute(
        select(
            scan_date.label("scan_date"),
            func.count(distinct(McpServerRegistry.server_id)).label("scanned"),
            func.coalesce(
                func.sum(McpServerRegistry.scan_count), 0
            ).label("scan_count_sum"),
        )
        .where(window_filter)
        .group_by(scan_date)
        .order_by(scan_date)
    ).all()

    series = [
        {
            "date": str(row.scan_date),
            "scanned": int(row.scanned or 0),
            "scan_count_sum": int(row.scan_count_sum or 0),
        }
        for row in daily_rows
    ]

    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count(distinct(McpServerRegistry.server_id)).label("servers"),
        )
        .where(window_filter)
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    sources = [
        {
            "registry_source": row.registry_source or "unknown",
            "servers": int(row.servers or 0),
        }
        for row in source_rows
    ]

    last_scan = db.execute(
        select(func.max(McpServerRegistry.last_scanned)).where(window_filter)
    ).scalar()

    return {
        "days": window_days,
        "series": series,
        "sources": sources,
        "last_scan_at": _as_iso(last_scan),
    }


get_scan_activity_timeline = scan_activity_timeline
build_timeline = scan_activity_timeline


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.utcnow().replace(microsecond=0)
    seeded = [
        McpServerRegistry(
            server_id="timeline-1",
            registry_source="alpha",
            last_scanned=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="timeline-2",
            registry_source="alpha",
            last_scanned=now - timedelta(days=1),
            scan_count=2,
        ),
        McpServerRegistry(
            server_id="timeline-3",
            registry_source="beta",
            last_scanned=now - timedelta(days=2),
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="timeline-4",
            registry_source="beta",
            last_scanned=now - timedelta(days=2, hours=1),
            scan_count=4,
        ),
    ]

    with TestSession() as session:
        session.add_all(seeded)
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/scanning/timeline")
    def timeline_endpoint(
        days: int = 14,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return scan_activity_timeline(db, days)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/scanning/timeline", params={"days": 14})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 14
        assert len(payload["series"]) == 3
        assert sum(item["scanned"] for item in payload["series"]) == 4
        assert len(payload["sources"]) == 2
        assert payload["last_scan_at"] is not None

    print("PASS")