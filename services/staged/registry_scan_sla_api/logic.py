from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: Iterable[float], percentile: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None

    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def compute_registry_scan_sla(session: Session, days: int = 30) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    servers = list(session.execute(select(McpServerRegistry)).scalars().all())

    grouped: Dict[Any, List[McpServerRegistry]] = {}
    for server in servers:
        grouped.setdefault(server.registry_source, []).append(server)

    sources: List[Dict[str, Any]] = []
    total_never_scanned = 0

    for registry_source in sorted(
        grouped, key=lambda value: (value is None, value or "")
    ):
        registry_servers = grouped[registry_source]
        never_scanned = [
            server for server in registry_servers if server.last_scanned is None
        ]
        scanned = [
            server for server in registry_servers if server.last_scanned is not None
        ]

        scan_ages: List[float] = []
        stale = 0
        for server in scanned:
            scanned_at = _as_utc(server.last_scanned)
            if scanned_at is None:
                continue
            age_hours = max(0.0, (now - scanned_at).total_seconds() / 3600.0)
            scan_ages.append(age_hours)
            if scanned_at < cutoff:
                stale += 1

        oldest_unscanned = sorted(
            never_scanned,
            key=lambda server: (
                _as_utc(server.first_seen) or datetime.max.replace(tzinfo=timezone.utc)
            ),
        )[:10]

        total_never_scanned += len(never_scanned)
        sources.append(
            {
                "registry_source": registry_source,
                "total": len(registry_servers),
                "never_scanned": len(never_scanned),
                "stale": stale,
                "median_scan_age_hours": (
                    median(scan_ages) if scan_ages else None
                ),
                "p90_scan_age_hours": _percentile(scan_ages, 0.9),
                "oldest_unscanned": [
                    {
                        "server_id": server.server_id,
                        "name": server.name,
                        "first_seen": server.first_seen,
                    }
                    for server in oldest_unscanned
                ],
            }
        )

    return {
        "days": days,
        "sources": sources,
        "totals": {
            "servers": len(servers),
            "never_scanned": total_never_scanned,
        },
    }


@router.get("/registry/scan-sla")
def get_registry_scan_sla(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_registry_scan_sla(session, days)


if __name__ == "__main__":
    from fastapi import FastAPI
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

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never Scanned",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=50),
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="recent",
                    name="Recently Scanned",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=20),
                    last_scanned=now - timedelta(days=1),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="old",
                    name="Old Scan",
                    registry_source="source-b",
                    first_seen=now - timedelta(days=100),
                    last_scanned=now - timedelta(days=40),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="moderate",
                    name="Moderate Scan",
                    registry_source="source-b",
                    first_seen=now - timedelta(days=30),
                    last_scanned=now - timedelta(days=5),
                    scan_count=4,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/registry/scan-sla?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 4
    assert payload["totals"]["never_scanned"] == 1
    assert sum(source["never_scanned"] for source in payload["sources"]) == 1
    assert any(source["stale"] == 1 for source in payload["sources"])
    print("PASS")