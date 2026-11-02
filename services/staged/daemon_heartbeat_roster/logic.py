"""Logic for the daemon heartbeat roster service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _health_table(db: Session) -> Table:
    """Reflect the real service_health table using the application metadata."""
    return Table(
        "service_health",
        McpServerRegistry.metadata,
        autoload_with=db.connection(),
        extend_existing=True,
    )


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket(age_seconds: float) -> str:
    if age_seconds <= 300:
        return "fresh"
    if age_seconds <= 3600:
        return "aging"
    return "stale"


def get_heartbeat_roster(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Build a heartbeat roster from the real service_health table."""
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    health = _health_table(db)
    rows = db.execute(
        select(
            health.c.service,
            health.c.last_heartbeat,
            health.c.status,
        )
    ).mappings().all()

    services = []
    fresh = 0
    aging = 0
    stale = 0

    for row in rows:
        heartbeat = _as_utc(row["last_heartbeat"])
        if heartbeat is None:
            age_seconds = 0.0
        else:
            age_seconds = max(
                0.0,
                (generated_at - heartbeat).total_seconds(),
            )

        bucket = _bucket(age_seconds)
        if bucket == "fresh":
            fresh += 1
        elif bucket == "aging":
            aging += 1
        else:
            stale += 1

        services.append(
            {
                "service": row["service"],
                "last_heartbeat": heartbeat,
                "age_seconds": age_seconds,
                "status": row["status"],
                "bucket": bucket,
            }
        )

    services.sort(key=lambda item: (str(item["service"] or ""),))
    return {
        "generated_at": generated_at,
        "total": len(services),
        "fresh": fresh,
        "aging": aging,
        "stale": stale,
        "services": services,
    }


def build_heartbeat_roster(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Alias used by the service router."""
    return get_heartbeat_roster(db, now=now)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import JSON, Column, Integer, String, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()
    test_health = Table(
        "service_health",
        test_metadata,
        Column("service", String, primary_key=True),
        Column("last_heartbeat", DateTime(timezone=True), nullable=False),
        Column("status", String, nullable=False),
        Column("meta", JSON),
    )
    test_metadata.create_all(test_engine)

    now = datetime.now(timezone.utc)
    with test_engine.begin() as connection:
        connection.execute(
            test_health.insert(),
            [
                {
                    "service": "alpha",
                    "last_heartbeat": now.replace(microsecond=0),
                    "status": "ok",
                    "meta": {},
                },
                {
                    "service": "beta",
                    "last_heartbeat": now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=600),
                    "status": "ok",
                    "meta": {},
                },
                {
                    "service": "gamma",
                    "last_heartbeat": now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=7200),
                    "status": "down",
                    "meta": {},
                },
            ],
        )

    test_session_factory = sessionmaker(bind=test_engine, autoflush=False)

    def override_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/ops/heartbeat-roster")
    def heartbeat_roster(session: Session = Depends(get_session)):
        return get_heartbeat_roster(session)

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/ops/heartbeat-roster")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["fresh"] == 1
    assert payload["aging"] == 1
    assert payload["stale"] == 1
    assert all(item["age_seconds"] >= 0 for item in payload["services"])
    print("PASS")