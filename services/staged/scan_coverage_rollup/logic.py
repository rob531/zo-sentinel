"""logic.py -- scan coverage computation over the real server registry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def _as_utc(value: datetime) -> datetime:
    """Normalize registry timestamps for consistent stale comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_scan_coverage(db: Session, days: int = 7) -> Dict:
    """Return scan coverage by registry source and global totals.

    A server is scanned when ``scan_count`` is positive, never scanned when
    ``last_scanned`` is null, and stale when its last scan is older than the
    requested number of days.
    """
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
        )
    ).all()

    grouped: Dict[str, Dict[str, int]] = {}
    totals = {"total": 0, "scanned": 0, "stale": 0, "never_scanned": 0}

    for registry_source, scan_count, last_scanned in rows:
        source_name = registry_source or "unknown"
        if source_name not in grouped:
            grouped[source_name] = {
                "total": 0,
                "scanned": 0,
                "stale": 0,
                "never_scanned": 0,
            }

        source = grouped[source_name]
        source["total"] += 1
        totals["total"] += 1

        if scan_count is not None and int(scan_count) > 0:
            source["scanned"] += 1
            totals["scanned"] += 1

        if last_scanned is None:
            source["never_scanned"] += 1
            totals["never_scanned"] += 1
        elif _as_utc(last_scanned) < cutoff:
            source["stale"] += 1
            totals["stale"] += 1

    sources = []
    for registry_source, values in sorted(grouped.items()):
        total = values["total"]
        coverage_pct = round((values["scanned"] / total) * 100, 2) if total else 0.0
        sources.append(
            {
                "registry_source": registry_source,
                "total": total,
                "scanned": values["scanned"],
                "stale": values["stale"],
                "never_scanned": values["never_scanned"],
                "coverage_pct": coverage_pct,
            }
        )

    total = totals["total"]
    totals["coverage_pct"] = (
        round((totals["scanned"] / total) * 100, 2) if total else 0.0
    )

    return {
        "days": days,
        "sources": sources,
        "totals": {
            "total": totals["total"],
            "scanned": totals["scanned"],
            "stale": totals["stale"],
            "never_scanned": totals["never_scanned"],
            "coverage_pct": totals["coverage_pct"],
        },
    }


def calculate_scan_coverage(db: Session, days: int = 7) -> Dict:
    """Compatibility alias for callers using the calculation naming."""
    return get_scan_coverage(db, days)


def scan_coverage_rollup(db: Session, days: int = 7) -> Dict:
    """Compatibility alias for callers using the service naming."""
    return get_scan_coverage(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="coverage-1",
                    registry_source="source-a",
                    scan_count=1,
                    last_scanned=now - timedelta(days=1),
                ),
                McpServerRegistry(
                    server_id="coverage-2",
                    registry_source="source-b",
                    scan_count=0,
                    last_scanned=None,
                ),
                McpServerRegistry(
                    server_id="coverage-3",
                    registry_source="source-c",
                    scan_count=1,
                    last_scanned=now - timedelta(days=30),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/coverage/scan-coverage")
    def coverage_endpoint(days: int = 7, db: Session = Depends(get_session)):
        return get_scan_coverage(db, days)

    response = TestClient(test_app).get("/api/coverage/scan-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert "sources" in payload
    assert "totals" in payload
    assert payload["totals"]["total"] == 3
    assert payload["totals"]["never_scanned"] == 1
    print("PASS")