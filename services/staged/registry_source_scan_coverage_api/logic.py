from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_registry_source_scan_coverage(db: Session, days: int) -> dict[str, Any]:
    cutoff = _utc_now() - timedelta(days=days)

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_seen,
            McpServerRegistry.scan_count,
        ).where(McpServerRegistry.registry_source.is_not(None))
    ).all()

    grouped: dict[str, list[tuple[Any, datetime | None, int]]] = {}
    for server_id, registry_source, last_seen, scan_count in rows:
        normalized_count = int(scan_count or 0)
        grouped.setdefault(registry_source, []).append(
            (server_id, _normalize_timestamp(last_seen), normalized_count)
        )

    sources: list[dict[str, Any]] = []
    for registry_source in sorted(grouped):
        servers = grouped[registry_source]
        total = len(servers)
        seen = sum(
            1
            for _, last_seen, _ in servers
            if last_seen is not None and last_seen >= cutoff
        )
        scanned = sum(1 for _, _, scan_count in servers if scan_count > 0)
        never_scanned = total - scanned
        median_scan_count = float(median([scan_count for _, _, scan_count in servers]))
        scan_coverage_pct = round((scanned / total) * 100, 2) if total else 0.0

        sources.append(
            {
                "registry_source": registry_source,
                "total": total,
                "seen": seen,
                "scanned": scanned,
                "never_scanned": never_scanned,
                "median_scan_count": median_scan_count,
                "scan_coverage_pct": scan_coverage_pct,
            }
        )

    return {"days": days, "sources": sources}


def get_source_scan_coverage(db: Session, days: int) -> dict[str, Any]:
    return get_registry_source_scan_coverage(db, days)


def compute_source_scan_coverage(db: Session, days: int) -> dict[str, Any]:
    return get_registry_source_scan_coverage(db, days)


def _self_test() -> None:
    import asyncio

    import httpx
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-alpha-1",
                    name="self-test-alpha-1",
                    registry_source="alpha",
                    last_seen=now,
                    scan_count=3,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="self-test-alpha-2",
                    name="self-test-alpha-2",
                    registry_source="alpha",
                    last_seen=now - timedelta(days=40),
                    scan_count=0,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="self-test-beta-1",
                    name="self-test-beta-1",
                    registry_source="beta",
                    last_seen=now,
                    scan_count=7,
                    risk_tier="medium",
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/source-scan-coverage")
    def source_scan_coverage(
        days: int = Query(default=30, ge=1, le=3650),
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_registry_source_scan_coverage(db, days)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(
                "/api/registry/source-scan-coverage", params={"days": 30}
            )

    response = asyncio.run(request())
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["days"] == 30
    by_source = {source["registry_source"]: source for source in payload["sources"]}
    assert by_source["alpha"]["total"] == 2
    assert by_source["alpha"]["seen"] == 1
    assert by_source["alpha"]["scanned"] == 1
    assert by_source["alpha"]["never_scanned"] == 1
    assert by_source["alpha"]["median_scan_count"] == 1.5
    assert by_source["alpha"]["scan_coverage_pct"] == 50.0
    assert by_source["beta"]["total"] == 1
    assert by_source["beta"]["seen"] == 1
    assert by_source["beta"]["never_scanned"] == 0
    assert by_source["beta"]["median_scan_count"] == 7.0

    print("PASS")


if __name__ == "__main__":
    _self_test()