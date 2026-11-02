"""Logic layer for the registry growth daily API."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_growth_daily(db: Session, days: int = 30) -> Dict:
    """Return daily first-seen counts and cumulative growth for the window."""
    if days < 1:
        raise ValueError("days must be at least 1")

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    rows = db.execute(
        select(
            func.date(McpServerRegistry.first_seen).label("first_seen_date"),
            McpServerRegistry.registry_source,
            func.count().label("server_count"),
        )
        .where(
            McpServerRegistry.first_seen >= start_datetime,
            McpServerRegistry.first_seen < end_datetime,
        )
        .group_by(
            func.date(McpServerRegistry.first_seen),
            McpServerRegistry.registry_source,
        )
    ).all()

    counts_by_date: Dict[str, Dict[str, int]] = {}
    for first_seen_date, registry_source, server_count in rows:
        day = str(first_seen_date)
        source = registry_source or "unknown"
        counts_by_date.setdefault(day, {})[source] = int(server_count)

    series: List[Dict] = []
    cumulative = 0
    total_sources: Dict[str, int] = {}

    for offset in range(days):
        day = (start_date + timedelta(days=offset)).isoformat()
        source_counts = counts_by_date.get(day, {})
        new_servers = sum(source_counts.values())
        cumulative += new_servers

        for source, count in source_counts.items():
            total_sources[source] = total_sources.get(source, 0) + count

        series.append(
            {
                "date": day,
                "new_servers": new_servers,
                "cumulative": cumulative,
                "sources": source_counts,
            }
        )

    return {
        "days": days,
        "series": series,
        "totals": {
            "new_servers": cumulative,
            "cumulative": cumulative,
            "sources": total_sources,
        },
    }


# Keep the service callable under the conventional lookup names used by thin routers.
registry_growth_daily = get_registry_growth_daily
compute_registry_growth = get_registry_growth_daily


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_db = TestingSession()
    today = date.today()
    yesterday = today - timedelta(days=1)
    seeded_rows = [
        ("growth-test-1", yesterday, "alpha"),
        ("growth-test-2", yesterday, "alpha"),
        ("growth-test-3", yesterday, "beta"),
        ("growth-test-4", today, "alpha"),
        ("growth-test-5", today, "beta"),
    ]
    for server_id, first_seen, registry_source in seeded_rows:
        test_db.add(
            McpServerRegistry(
                server_id=server_id,
                first_seen=datetime.combine(first_seen, datetime.min.time()),
                registry_source=registry_source,
            )
        )
    test_db.commit()

    that_app.dependency_overrides[get_session] = lambda: test_db

    @that_app.get("/api/registry/growth-daily")
    def local_growth_daily(
        days: int = Query(30, ge=1, le=3650),
        db: Session = Depends(get_session),
    ) -> Dict:
        return get_registry_growth_daily(db, days)

    response = TestClient(that_app).get(
        "/api/registry/growth-daily", params={"days": 2}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 2
    assert len(payload["series"]) == 2
    assert sum(item["new_servers"] for item in payload["series"]) == 5
    assert payload["series"][0]["new_servers"] == 3
    assert payload["series"][1]["new_servers"] == 2
    assert payload["totals"]["new_servers"] == 5
    assert payload["totals"]["cumulative"] == 5

    print("PASS")