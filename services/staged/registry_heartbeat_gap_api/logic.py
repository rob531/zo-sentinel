from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session


_REGISTRY_COLUMNS = (
    McpServerRegistry.server_id,
    McpServerRegistry.name,
    McpServerRegistry.registry_source,
    McpServerRegistry.last_seen,
    McpServerRegistry.last_scanned,
    McpServerRegistry.scan_count,
    McpServerRegistry.verdict,
    McpServerRegistry.risk_tier,
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    age = now - _as_utc(value)
    return round(max(age.total_seconds(), 0) / 3600, 2)


def get_heartbeat_gap(
    session: Session = Depends(get_session),
    hours: int = 168,
) -> dict[str, Any]:
    threshold_hours = max(0, int(hours))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=threshold_hours)

    total_servers = int(
        session.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()
    )
    never_seen = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_seen.is_(None))
        ).scalar_one()
    )

    stale_rows = session.execute(
        select(*_REGISTRY_COLUMNS).where(
            or_(
                McpServerRegistry.last_seen.is_(None),
                McpServerRegistry.last_seen < cutoff,
            )
        )
    ).mappings().all()

    scan_behind_registry = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(
                McpServerRegistry.scan_count > 1,
                McpServerRegistry.last_seen.is_not(None),
                McpServerRegistry.last_scanned.is_not(None),
                McpServerRegistry.last_scanned < McpServerRegistry.last_seen,
            )
        ).scalar_one()
    )

    gap_servers = []
    for row in stale_rows:
        last_seen = _as_utc(row["last_seen"])
        if last_seen is None or last_seen >= cutoff:
            continue
        gap_servers.append(
            {
                "server_id": row["server_id"],
                "name": row["name"],
                "registry_source": row["registry_source"],
                "last_seen": row["last_seen"],
                "last_scanned": row["last_scanned"],
                "age_hours": _age_hours(last_seen, now),
                "scan_count": row["scan_count"],
                "verdict": row["verdict"],
                "risk_tier": row["risk_tier"],
            }
        )

    gap_servers.sort(key=lambda item: item["age_hours"], reverse=True)
    gap_servers = gap_servers[:50]

    return {
        "generated_at": now.isoformat(),
        "threshold_hours": threshold_hours,
        "total_servers": total_servers,
        "never_seen": never_seen,
        "stale_count": len(gap_servers),
        "scan_behind_registry": scan_behind_registry,
        "gap_servers": gap_servers,
    }


def get_registry_heartbeat_gap(
    session: Session = Depends(get_session),
    hours: int = 168,
) -> dict[str, Any]:
    return get_heartbeat_gap(session=session, hours=hours)


def compute_heartbeat_gap(
    session: Session = Depends(get_session),
    hours: int = 168,
) -> dict[str, Any]:
    return get_heartbeat_gap(session=session, hours=hours)


def get_heartbeat_gap_report(
    session: Session = Depends(get_session),
    hours: int = 168,
) -> dict[str, Any]:
    return get_heartbeat_gap(session=session, hours=hours)


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
    McpServerRegistry.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh",
                    name="Fresh",
                    registry_source="test",
                    last_seen=now - timedelta(hours=1),
                    first_seen=now - timedelta(days=2),
                    last_scanned=now - timedelta(hours=1),
                    scan_count=1,
                    verdict="ok",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="stale",
                    name="Stale",
                    registry_source="test",
                    last_seen=now - timedelta(days=30),
                    first_seen=now - timedelta(days=31),
                    last_scanned=now - timedelta(days=30),
                    scan_count=1,
                    verdict="unknown",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="never",
                    name="Never seen",
                    registry_source="test",
                    last_seen=None,
                    first_seen=None,
                    last_scanned=None,
                    scan_count=0,
                    verdict="unknown",
                    risk_tier="unknown",
                ),
                McpServerRegistry(
                    server_id="scan-behind",
                    name="Scan behind",
                    registry_source="test",
                    last_seen=now - timedelta(hours=1),
                    first_seen=now - timedelta(days=2),
                    last_scanned=now - timedelta(hours=2),
                    scan_count=2,
                    verdict="ok",
                    risk_tier="low",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/heartbeat-gap")
    def heartbeat_gap_endpoint(hours: int = 168):
        return get_heartbeat_gap(session=SessionLocal(), hours=hours)

    test_app.dependency_overrides[get_session] = lambda: SessionLocal()
    with TestClient(test_app) as client:
        response = client.get("/api/registry/heartbeat-gap")
        assert response.status_code == 200
        payload = response.json()
        assert payload["never_seen"] == 1
        assert payload["stale_count"] >= 1
        assert payload["scan_behind_registry"] >= 1
        ages = [item["age_hours"] for item in payload["gap_servers"]]
        assert ages == sorted(ages, reverse=True)

    engine.dispose()
    print("PASS")