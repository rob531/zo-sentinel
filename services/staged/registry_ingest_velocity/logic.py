"""Logic for registry ingest velocity calculations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def registry_ingest_velocity(db: Session, days: int = 30) -> Dict[str, object]:
    """Return recent registry ingestion activity and per-source statistics."""
    days = max(1, int(days))
    first_day = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()

    daily_rows = db.execute(
        select(
            func.date(McpServerRegistry.first_seen),
            func.count(McpServerRegistry.server_id),
        )
        .where(func.date(McpServerRegistry.first_seen) >= first_day)
        .group_by(func.date(McpServerRegistry.first_seen))
        .order_by(func.date(McpServerRegistry.first_seen))
    ).all()

    series: List[Dict[str, object]] = []
    for day, count in daily_rows:
        if isinstance(day, (date, datetime)):
            day_value = day.date().isoformat() if isinstance(day, datetime) else day.isoformat()
        else:
            day_value = str(day)
        series.append({"date": day_value, "count": int(count)})

    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id),
            func.avg(McpServerRegistry.scan_count),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    sources: List[Dict[str, object]] = []
    for source, total, average in source_rows:
        sources.append(
            {
                "registry_source": source or "unknown",
                "total": int(total),
                "avg_scan_count": float(average or 0),
            }
        )

    return {
        "days": days,
        "series": series,
        "sources": sources,
        "total_new": sum(int(item["count"]) for item in series),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_registry_ingest_velocity(db: Session, days: int = 30) -> Dict[str, object]:
    """Compatibility wrapper for the registry ingest velocity service."""
    return registry_ingest_velocity(db, days)


def get_ingest_velocity(db: Session, days: int = 30) -> Dict[str, object]:
    """Compatibility wrapper for the registry ingest velocity service."""
    return registry_ingest_velocity(db, days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    seeded = [
        McpServerRegistry(
            server_id="velocity-1",
            name="Velocity One",
            registry_source="alpha",
            first_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="velocity-2",
            name="Velocity Two",
            registry_source="alpha",
            first_seen=now,
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="velocity-3",
            name="Velocity Three",
            registry_source="beta",
            first_seen=now - timedelta(days=1),
            scan_count=5,
        ),
        McpServerRegistry(
            server_id="velocity-4",
            name="Velocity Four",
            registry_source="beta",
            first_seen=now - timedelta(days=2),
            scan_count=7,
        ),
        McpServerRegistry(
            server_id="velocity-5",
            name="Velocity Five",
            registry_source="beta",
            first_seen=now - timedelta(days=2),
            scan_count=9,
        ),
    ]
    session.add_all(seeded)
    session.commit()

    that_app = FastAPI()

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/ingest-velocity")
    def ingest_velocity(days: int = 30, db: Session = Depends(get_session)):
        return registry_ingest_velocity(db, days)

    response = TestClient(that_app).get("/api/registry/ingest-velocity?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series"]) == 3
    assert max(item["count"] for item in payload["series"]) == 2
    assert len(payload["sources"]) == 2
    assert sum(item["total"] for item in payload["sources"]) == 5
    assert payload["total_new"] == 5
    print("PASS")