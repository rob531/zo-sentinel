"""logic.py -- registry freshness SLA computation over the real app data layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SLA_WINDOW_DAYS = 30


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: datetime | None, now: datetime) -> float | None:
    value = _as_utc(value)
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds() / 86400.0)


def _age_bucket(age_days: float | None) -> str:
    if age_days is None or age_days > 30:
        return ">30d"
    if age_days < 1:
        return "<24h"
    if age_days <= 7:
        return "1-7d"
    return "7-30d"


def get_registry_freshness_sla(db: Session) -> Dict[str, Any]:
    """Return registry freshness SLA statistics grouped by registry source."""
    rows = db.execute(select(McpServerRegistry)).scalars().all()
    now = datetime.now(timezone.utc)
    grouped: Dict[str, list[McpServerRegistry]] = {}

    for row in rows:
        source = row.registry_source or "unknown"
        grouped.setdefault(source, []).append(row)

    sources: list[Dict[str, Any]] = []
    for source, servers in grouped.items():
        age_buckets = {"<24h": 0, "1-7d": 0, "7-30d": 0, ">30d": 0}
        breach_count = 0
        oldest_age = 0.0

        for server in servers:
            age = _age_days(server.last_assessed, now)
            if age is None:
                age = _age_days(server.first_seen, now)
            if age is None:
                bucket = ">30d"
                is_breach = True
            else:
                bucket = _age_bucket(age)
                oldest_age = max(oldest_age, age)
                is_breach = age > SLA_WINDOW_DAYS
            age_buckets[bucket] += 1
            if is_breach:
                breach_count += 1

        total = len(servers)
        sources.append(
            {
                "registry_source": source,
                "total": total,
                "breach_count": breach_count,
                "sla_breach_rate": (breach_count / total) if total else 0.0,
                "age_buckets": age_buckets,
                "oldest_unassessed_days": int(oldest_age),
            }
        )

    return {"sources": sources}


# Compatibility aliases for callers using the shorter service name.
get_registry_freshness = get_registry_freshness_sla
registry_freshness_sla = get_registry_freshness_sla


if __name__ == "__main__":
    from datetime import timedelta

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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    test_db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="fresh-server",
                registry_source="source-fresh",
                first_seen=now - timedelta(days=2),
                last_assessed=now - timedelta(days=1),
            ),
            McpServerRegistry(
                server_id="medium-server",
                registry_source="source-medium",
                first_seen=now - timedelta(days=11),
                last_assessed=now - timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="stale-server",
                registry_source="source-stale",
                first_seen=now - timedelta(days=61),
                last_assessed=now - timedelta(days=60),
            ),
        ]
    )
    test_db.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/freshness-sla")
    def freshness_sla(db: Session = Depends(get_session)):
        return get_registry_freshness_sla(db)

    that_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()
    response = TestClient(that_app).get("/api/registry/freshness-sla")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 3
    by_source = {item["registry_source"]: item for item in payload["sources"]}
    assert by_source["source-stale"]["breach_count"] == 1
    assert by_source["source-fresh"]["breach_count"] == 0
    assert by_source["source-medium"]["breach_count"] == 0
    print("PASS")