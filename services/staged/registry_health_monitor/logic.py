"""Data and computation layer for MCP registry health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_registry_health(
    db: Session,
    source: Optional[str] = None,
    days: int = 30,
) -> Dict:
    """Compute registry health from the authoritative application registry."""
    if days < 0:
        raise ValueError("days must be non-negative")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    normalized_source = source.strip() if isinstance(source, str) and source.strip() else None

    statement = select(McpServerRegistry)
    if normalized_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == normalized_source
        )

    servers = list(db.execute(statement).scalars().all())

    by_source: Dict[str, Dict[str, int]] = {}
    by_risk_tier: Dict[str, int] = {}
    recently_seen: List[Dict] = []

    for server in servers:
        registry_source = server.registry_source or "unknown"
        source_counts = by_source.setdefault(
            registry_source,
            {"registry_source": registry_source, "total": 0, "stale": 0, "never_scanned": 0},
        )
        source_counts["total"] += 1

        last_seen = _as_utc(server.last_seen)
        if last_seen is not None and last_seen < cutoff:
            source_counts["stale"] += 1

        if server.last_scanned is None or server.scan_count == 0:
            source_counts["never_scanned"] += 1

        risk_tier = server.risk_tier or "unknown"
        by_risk_tier[risk_tier] = by_risk_tier.get(risk_tier, 0) + 1

        if last_seen is not None:
            recently_seen.append(
                {
                    "server_id": server.server_id,
                    "name": server.name,
                    "registry_source": registry_source,
                    "risk_tier": risk_tier,
                    "last_seen": last_seen,
                    "scan_count": server.scan_count or 0,
                    "_sort_seen": last_seen,
                }
            )

    recently_seen.sort(
        key=lambda item: (item["_sort_seen"], item["server_id"]),
        reverse=True,
    )
    for item in recently_seen:
        item.pop("_sort_seen", None)

    source_rows = sorted(
        by_source.values(),
        key=lambda item: item["registry_source"],
    )

    return {
        "days": days,
        "filter_source": normalized_source,
        "total": len(servers),
        "by_source": source_rows,
        "by_risk_tier": by_risk_tier,
        "recently_seen": recently_seen[:10],
        "generated_at": now,
    }


compute_registry_health = get_registry_health
calculate_registry_health = get_registry_health


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/health")
    def registry_health(
        source: Optional[str] = None,
        days: int = 30,
        db: Session = Depends(get_session),
    ):
        return get_registry_health(db, source=source, days=days)

    current = datetime.now(timezone.utc)
    with Session(test_engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a1",
                    name="Registry A One",
                    registry_source="registry-a",
                    first_seen=current - timedelta(days=20),
                    last_seen=current - timedelta(days=1),
                    last_scanned=current - timedelta(days=1),
                    scan_count=3,
                    risk_tier="low",
                    verdict="healthy",
                ),
                McpServerRegistry(
                    server_id="server-a2",
                    name="Registry A Two",
                    registry_source="registry-a",
                    first_seen=current - timedelta(days=90),
                    last_seen=current - timedelta(days=60),
                    last_scanned=current - timedelta(days=60),
                    scan_count=2,
                    risk_tier="high",
                    verdict="stale",
                ),
                McpServerRegistry(
                    server_id="server-b1",
                    name="Registry B One",
                    registry_source="registry-b",
                    first_seen=current - timedelta(days=10),
                    last_seen=current - timedelta(days=2),
                    last_scanned=current - timedelta(days=2),
                    scan_count=1,
                    risk_tier="medium",
                    verdict="healthy",
                ),
                McpServerRegistry(
                    server_id="server-b2",
                    name="Registry B Two",
                    registry_source="registry-b",
                    first_seen=current - timedelta(days=5),
                    last_seen=current - timedelta(days=3),
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="low",
                    verdict="unscanned",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get(
        "/api/registry/health",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert sum(item["never_scanned"] for item in payload["by_source"]) == 1
    assert {item["registry_source"] for item in payload["by_source"]} == {
        "registry-a",
        "registry-b",
    }
    print("PASS")