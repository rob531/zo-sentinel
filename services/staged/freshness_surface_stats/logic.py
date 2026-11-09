"""Freshness surface statistics over the real application database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat, McpServerRegistry

STALE_AFTER_SECONDS = 3600


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def registry_source_counts(db: Session) -> Dict[str, int]:
    """Return the number of registry servers for each registry source."""
    rows = db.execute(
        select(McpServerRegistry.registry_source, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.registry_source)
    ).all()
    return {source or "unknown": int(count) for source, count in rows}


def get_surface_stats(db: Session) -> Dict[str, Any]:
    """Build freshness statistics for every recorded surface."""
    generated_at = datetime.now(timezone.utc)
    stale_before = generated_at - timedelta(seconds=STALE_AFTER_SECONDS)

    stats = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces = []
    for stat in stats:
        computed_at = _utc_datetime(stat.computed_at)
        surfaces.append(
            {
                "surface": stat.surface,
                "computed_at": computed_at.isoformat(),
                "compute_seconds": stat.compute_seconds,
                "payload": stat.payload,
                "stale": computed_at < stale_before,
            }
        )

    return {
        "surfaces": surfaces,
        "generated_at": generated_at.isoformat(),
        "registry_source_counts": registry_source_counts(db),
    }


def get_freshness_surface_stats(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for callers using the full service name."""
    return get_surface_stats(db)


def surface_stats(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for callers using the concise service name."""
    return get_surface_stats(db)


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
    FreshnessStat.__table__.create(engine, checkfirst=True)
    McpServerRegistry.__table__.create(engine, checkfirst=True)

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()
    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            FreshnessStat(
                surface="advisories",
                payload={"count": 12},
                computed_at=now - timedelta(seconds=7200),
                compute_seconds=1.25,
            ),
            FreshnessStat(
                surface="mcp_registry",
                payload={"count": 4},
                computed_at=now - timedelta(seconds=120),
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="threat_intel",
                payload={"count": 7},
                computed_at=now - timedelta(seconds=600),
                compute_seconds=0.5,
            ),
            McpServerRegistry(server_id="registry-1", registry_source="primary"),
            McpServerRegistry(server_id="registry-2", registry_source="primary"),
            McpServerRegistry(server_id="registry-3", registry_source="secondary"),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db: Session = Depends(get_session)):
        return get_surface_stats(db)

    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert sum(1 for surface in body["surfaces"] if surface["stale"]) == 1
    assert body["registry_source_counts"] == {"primary": 2, "secondary": 1}
    print("PASS")