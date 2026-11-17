from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = (
    ("0-7", 0, 7),
    ("8-30", 8, 30),
    ("31-90", 31, 90),
    ("91-365", 91, 365),
    ("365+", 365, None),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: datetime | None, now: datetime) -> int | None:
    value = _as_utc(value)
    if value is None:
        return None
    return max(0, (now - value).days)


def get_registry_aging(session: Session) -> dict[str, Any]:
    now = _utc_now()
    rows = session.execute(
        select(
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.registry_source,
        )
    ).all()

    counts = {bucket: 0 for bucket, _, _ in BUCKETS}
    never_scanned = 0
    stale_30d = 0
    source_counts: dict[Any, int] = {}

    for first_seen, last_seen, last_scanned, registry_source in rows:
        age = _age_days(first_seen, now)
        if age is not None:
            if age <= 7:
                counts["0-7"] += 1
            elif age <= 30:
                counts["8-30"] += 1
            elif age <= 90:
                counts["31-90"] += 1
            elif age < 365:
                counts["91-365"] += 1
            else:
                counts["365+"] += 1

        if last_scanned is None:
            never_scanned += 1

        last_seen_age = _age_days(last_seen, now)
        if last_seen_age is None or last_seen_age > 30:
            stale_30d += 1

        source_counts[registry_source] = source_counts.get(registry_source, 0) + 1

    total_servers = len(rows)
    never_scanned_pct = (
        round((never_scanned / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    return {
        "generated_at": now.isoformat(),
        "total_servers": total_servers,
        "buckets": [
            {
                "bucket": bucket,
                "min_days": min_days,
                "max_days": max_days,
                "count": counts[bucket],
            }
            for bucket, min_days, max_days in BUCKETS
        ],
        "never_scanned": {
            "count": never_scanned,
            "pct": never_scanned_pct,
        },
        "by_source": [
            {"registry_source": registry_source, "count": count}
            for registry_source, count in sorted(
                source_counts.items(),
                key=lambda item: (item[0] is not None, str(item[0])),
            )
        ],
        "stale_30d": stale_30d,
    }


compute_registry_aging = get_registry_aging
get_aging_summary = get_registry_aging


__all__ = [
    "BUCKETS",
    "compute_registry_aging",
    "get_aging_summary",
    "get_registry_aging",
]


def _self_test() -> None:
    engine = create_test_engine = None
    del create_test_engine

    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = _utc_now()
    test_rows = [
        ("server-1", 3, True, "github"),
        ("server-2", 15, True, "github"),
        ("server-3", 60, True, "manual"),
        ("server-4", 120, True, "manual"),
        ("server-5", 400, False, "npm"),
        ("server-6", 5, False, "npm"),
    ]

    with session_factory() as session:
        for server_id, age, scanned, source in test_rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    url=f"https://example.test/{server_id}",
                    first_seen=now - timedelta(days=age),
                    last_seen=now,
                    last_scanned=now if scanned else None,
                    scan_count=1 if scanned else 0,
                    registry_source=source,
                    risk_tier="medium",
                )
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/aging")
    def registry_aging(session: Session = Depends(get_session)):
        return get_registry_aging(session)

    def override_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/aging")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6
        assert payload["never_scanned"]["count"] == 2
        assert {item["bucket"] for item in payload["buckets"]} == {
            "0-7",
            "8-30",
            "31-90",
            "91-365",
            "365+",
        }

    print("PASS")


if __name__ == "__main__":
    _self_test()