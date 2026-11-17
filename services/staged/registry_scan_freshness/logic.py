from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _calculate_scan_freshness(session: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_assessed,
            McpServerRegistry.scan_count,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
    ).all()

    buckets = {
        "never_scanned": 0,
        "over_24h": 0,
        "over_7d": 0,
        "over_30d": 0,
    }
    sources: dict[str, dict[str, float | int]] = {}
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    for row in rows:
        server_id, name, registry_source, last_scanned, _, _, scan_count, risk_tier, _ = row
        scanned_at = _as_utc_naive(last_scanned)
        hours_since_scan: float | None = None

        if scanned_at is None:
            buckets["never_scanned"] += 1
        else:
            hours_since_scan = round(
                max(0.0, (now - scanned_at).total_seconds() / 3600.0), 2
            )
            if hours_since_scan > 24 * 30:
                buckets["over_30d"] += 1
            elif hours_since_scan > 24 * 7:
                buckets["over_7d"] += 1
            elif hours_since_scan > 24:
                buckets["over_24h"] += 1

        source = registry_source if registry_source is not None else "unknown"
        source_stats = sources.setdefault(
            source,
            {"total": 0, "stale_over_7d": 0, "scan_count_sum": 0},
        )
        source_stats["total"] += 1
        source_stats["scan_count_sum"] += int(scan_count or 0)
        if hours_since_scan is not None and hours_since_scan > 24 * 7:
            source_stats["stale_over_7d"] += 1

        sort_key = (
            scanned_at is None,
            scanned_at or datetime.max,
            str(server_id),
        )
        ranked.append(
            (
                sort_key,
                {
                    "server_id": server_id,
                    "name": name,
                    "registry_source": source,
                    "hours_since_scan": hours_since_scan,
                    "scan_count": int(scan_count or 0),
                    "risk_tier": risk_tier,
                },
            )
        )

    by_source = []
    for source in sorted(sources):
        stats = sources[source]
        total = int(stats["total"])
        by_source.append(
            {
                "registry_source": source,
                "total": total,
                "stale_over_7d": int(stats["stale_over_7d"]),
                "mean_scan_count": (
                    round(float(stats["scan_count_sum"]) / total, 2) if total else 0.0
                ),
            }
        )

    ranked.sort(key=lambda item: item[0])
    stalest = [item[1] for item in ranked[:20]]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": buckets,
        "by_source": by_source,
        "stalest": stalest,
    }


def get_registry_scan_freshness(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _calculate_scan_freshness(session)


get_scan_freshness = get_registry_scan_freshness
compute_scan_freshness = get_registry_scan_freshness
compute_registry_scan_freshness = get_registry_scan_freshness
build_scan_freshness = get_registry_scan_freshness
build_registry_scan_freshness = get_registry_scan_freshness
get_scan_freshness_data = get_registry_scan_freshness


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    seed_now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="test",
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="unknown",
                ),
                McpServerRegistry(
                    server_id="recent",
                    name="Recently scanned",
                    registry_source="test",
                    last_scanned=seed_now - timedelta(hours=2),
                    scan_count=2,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="stale",
                    name="Stale server",
                    registry_source="test",
                    last_scanned=seed_now - timedelta(days=40),
                    scan_count=7,
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-freshness")
    def scan_freshness_endpoint(session: Session = Depends(get_session)):
        return get_registry_scan_freshness(session)

    response = TestClient(test_app).get("/api/registry/scan-freshness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["buckets"]["never_scanned"] == 1
    assert payload["buckets"]["over_30d"] == 1
    assert sum(item["total"] for item in payload["by_source"]) == 3
    print("PASS")