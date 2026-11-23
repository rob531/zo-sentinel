"""Data and computation layer for the freshness SLA surface service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat, McpServerRegistry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(value: datetime, now: datetime) -> float:
    return round((now - _as_utc(value)).total_seconds() / 3600.0, 2)


def get_freshness_sla_surfaces(db: Session) -> Dict[str, Any]:
    """Return freshness verdicts for every registered freshness surface."""
    now = _utc_now()
    stats = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces: list[Dict[str, Any]] = []
    for stat in stats:
        age_hours = _age_hours(stat.computed_at, now)
        if age_hours <= 24:
            verdict = "FRESH"
        elif age_hours <= 72:
            verdict = "STALE"
        else:
            verdict = "EXPIRED"

        surfaces.append(
            {
                "surface": stat.surface,
                "computed_at": stat.computed_at,
                "age_hours": age_hours,
                "verdict": verdict,
                "compute_seconds": stat.compute_seconds,
            }
        )

    return {"surfaces": surfaces}


def get_stale_servers(db: Session, stale_days: int = 7) -> Dict[str, Any]:
    """Return servers whose last scan is older than the requested SLA window."""
    now = _utc_now()
    cutoff = now - timedelta(days=max(0, int(stale_days)))
    servers = db.execute(select(McpServerRegistry)).scalars().all()

    stale_servers: list[Dict[str, Any]] = []
    stale_by_source: Dict[str, int] = {}

    for server in servers:
        if server.last_scanned is None:
            continue

        last_scanned = _as_utc(server.last_scanned)
        if last_scanned >= cutoff:
            continue

        source = server.registry_source or "unknown"
        stale_by_source[source] = stale_by_source.get(source, 0) + 1
        stale_servers.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "last_scanned": server.last_scanned,
                "age_hours": _age_hours(server.last_scanned, now),
            }
        )

    stale_servers.sort(key=lambda item: (item["age_hours"], item["server_id"]))
    return {
        "stale_days": max(0, int(stale_days)),
        "total_servers": len(servers),
        "stale_servers": len(stale_servers),
        "stale_by_source": stale_by_source,
        "servers": stale_servers,
    }


# Stable aliases for routers and existing service integrations.
get_sla_surfaces = get_freshness_sla_surfaces
get_sla_servers = get_stale_servers
get_freshness_surfaces = get_freshness_sla_surfaces
get_surfaces = get_freshness_sla_surfaces


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(bind=engine)
    McpServerRegistry.__table__.create(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            FreshnessStat(
                surface="alpha",
                payload={"status": "ok"},
                computed_at=now - timedelta(hours=2),
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="beta",
                payload={"status": "ok"},
                computed_at=now - timedelta(hours=100),
                compute_seconds=0.5,
            ),
            McpServerRegistry(
                server_id="server-stale",
                name="Stale Server",
                registry_source="registry-a",
                last_scanned=now - timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="server-fresh-1",
                name="Fresh Server One",
                registry_source="registry-a",
                last_scanned=now - timedelta(days=1),
            ),
            McpServerRegistry(
                server_id="server-fresh-2",
                name="Fresh Server Two",
                registry_source="registry-b",
                last_scanned=now - timedelta(days=2),
            ),
        ]
    )
    db.commit()
    db.close()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness/sla/surfaces")
    def freshness_sla_surfaces(session: Session = Depends(get_session)):
        return get_freshness_sla_surfaces(session)

    @test_app.get("/api/freshness/sla/servers")
    def freshness_sla_servers(
        stale_days: int = 7, session: Session = Depends(get_session)
    ):
        return get_stale_servers(session, stale_days)

    with TestClient(test_app) as client:
        surfaces_response = client.get("/api/freshness/sla/surfaces")
        servers_response = client.get("/api/freshness/sla/servers?stale_days=7")

        assert surfaces_response.status_code == 200
        assert servers_response.status_code == 200

        surfaces = surfaces_response.json()["surfaces"]
        servers = servers_response.json()

        assert len(surfaces) == 2
        assert sum(item["verdict"] == "EXPIRED" for item in surfaces) == 1
        assert servers["stale_servers"] == 1

    print("PASS")