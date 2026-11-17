"""Data and computation layer for registry scan coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceScanCoverage(BaseModel):
    registry_source: str
    total: int
    scanned: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class ScanCoverageResponse(BaseModel):
    total: int
    scanned: int
    never_scanned: int
    stale: int
    sources: List[SourceScanCoverage]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_scan_coverage(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Compute registry scan coverage from the authoritative registry table."""
    current_time = _as_utc(now) or _utc_now()
    cutoff = current_time - timedelta(days=7)

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
    ).all()

    source_values: Dict[str, List[Any]] = {}
    total = 0
    scanned = 0
    never_scanned = 0
    stale = 0

    for registry_source, last_scanned, scan_count in rows:
        source = registry_source or "unknown"
        bucket = source_values.setdefault(source, {"scanned": 0, "never": 0, "stale": 0, "scan_counts": []})

        total += 1
        bucket["total"] = bucket.get("total", 0) + 1
        bucket["scan_counts"].append(float(scan_count or 0))

        scanned_at = _as_utc(last_scanned)
        if scanned_at is None:
            never_scanned += 1
            bucket["never"] += 1
        else:
            scanned += 1
            bucket["scanned"] += 1
            if scanned_at < cutoff:
                stale += 1
                bucket["stale"] += 1

    sources: List[SourceScanCoverage] = []
    for source in sorted(source_values):
        bucket = source_values[source]
        counts = bucket["scan_counts"]
        average = round(sum(counts) / len(counts), 2) if counts else 0.0
        sources.append(
            SourceScanCoverage(
                registry_source=source,
                total=int(bucket.get("total", 0)),
                scanned=int(bucket["scanned"]),
                never_scanned=int(bucket["never"]),
                stale=int(bucket["stale"]),
                avg_scan_count=float(average),
            )
        )

    return {
        "total": int(total),
        "scanned": int(scanned),
        "never_scanned": int(never_scanned),
        "stale": int(stale),
        "sources": sources,
    }


def get_scan_coverage(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return scan coverage for the registry."""
    return compute_scan_coverage(db)


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

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
    db = SessionLocal()
    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="scan-test-1",
                    name="recently scanned",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=30),
                    last_seen=now - timedelta(days=1),
                    last_scanned=now - timedelta(days=1),
                    scan_count=3,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="scan-test-2",
                    name="never scanned",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=30),
                    last_seen=now - timedelta(days=1),
                    last_scanned=None,
                    scan_count=None,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="scan-test-3",
                    name="stale registry",
                    registry_source="source-b",
                    first_seen=now - timedelta(days=30),
                    last_seen=now - timedelta(days=10),
                    last_scanned=now - timedelta(days=10),
                    scan_count=1,
                    risk_tier="high",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    test_app = FastAPI()

    @test_app.get("/api/registry/scan-coverage", response_model=ScanCoverageResponse)
    def scan_coverage_endpoint(db: Session = Depends(get_session)):
        return compute_scan_coverage(db)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/registry/scan-coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["never_scanned"] == 1
    assert payload["scanned"] == 2
    assert payload["stale"] == 1
    source_a = next(
        item for item in payload["sources"] if item["registry_source"] == "source-a"
    )
    assert source_a["total"] == 2
    assert source_a["never_scanned"] == 1
    print("PASS")
    engine.dispose()


if __name__ == "__main__":
    _self_test()