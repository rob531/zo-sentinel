"""Data and computation layer for the scan cadence API."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def scan_cadence(db: Session) -> Dict[str, Any]:
    """Build per-source scan-staleness buckets from the real registry."""
    generated_at = datetime.now(timezone.utc)
    stale_7d_cutoff = generated_at - timedelta(days=7)
    stale_30d_cutoff = generated_at - timedelta(days=30)

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
    ).all()

    grouped: dict[str, list[tuple[datetime | None, int]]] = defaultdict(list)
    totals: dict[str, int] = {
        "never_scanned": 0,
        "stale_7d": 0,
        "stale_30d": 0,
        "fresh": 0,
    }
    total_scan_count = 0

    for source, last_scanned, scan_count in rows:
        source_name = source or "unknown"
        count = int(scan_count or 0)
        scanned_at = _as_utc(last_scanned)
        grouped[source_name].append((scanned_at, count))
        total_scan_count += count

        if scanned_at is None:
            bucket = "never_scanned"
        elif scanned_at < stale_30d_cutoff:
            bucket = "stale_30d"
        elif scanned_at < stale_7d_cutoff:
            bucket = "stale_7d"
        else:
            bucket = "fresh"
        totals[bucket] += 1

    buckets: list[Dict[str, Any]] = []
    for source_name in sorted(grouped):
        entries = grouped[source_name]
        counts = {
            "never_scanned": 0,
            "stale_7d": 0,
            "stale_30d": 0,
            "fresh": 0,
        }
        source_scan_count = 0
        for scanned_at, count in entries:
            source_scan_count += count
            if scanned_at is None:
                bucket = "never_scanned"
            elif scanned_at < stale_30d_cutoff:
                bucket = "stale_30d"
            elif scanned_at < stale_7d_cutoff:
                bucket = "stale_7d"
            else:
                bucket = "fresh"
            counts[bucket] += 1

        source_total = len(entries)
        buckets.append(
            {
                "source": source_name,
                **counts,
                "total": source_total,
                "avg_scan_count": source_scan_count / source_total,
            }
        )

    total_servers = len(rows)
    totals["total"] = total_servers
    totals["avg_scan_count"] = (
        total_scan_count / total_servers if total_servers else 0.0
    )

    return {
        "generated_at": generated_at,
        "buckets": buckets,
        "totals": totals,
    }


def get_scan_cadence(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for the router."""
    return scan_cadence(db)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="cadence-never",
                    name="Never scanned",
                    registry_source="acceptance",
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="cadence-old",
                    name="Forty days old",
                    registry_source="acceptance",
                    last_scanned=now - timedelta(days=40),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="cadence-fresh",
                    name="Scanned today",
                    registry_source="acceptance",
                    last_scanned=now,
                    scan_count=4,
                ),
            ]
        )
        db.commit()

    def override_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/servers/scan-cadence")
    def scan_cadence_endpoint(db: Session = Depends(get_session)):
        return scan_cadence(db)

    response = TestClient(test_app).get("/api/servers/scan-cadence")
    assert response.status_code == 200
    payload = response.json()
    bucket = payload["buckets"][0]
    assert bucket["never_scanned"] == 1
    assert bucket["stale_30d"] == 1
    assert bucket["fresh"] == 1
    assert payload["totals"]["total"] == 3
    print("PASS")