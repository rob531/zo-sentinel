"""logic.py -- scan-count histogram computation for the server registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = ("0", "1", "2-3", "4-9", "10+")


def _scan_count_band() -> Any:
    return case(
        (McpServerRegistry.scan_count.is_(None), "0"),
        (McpServerRegistry.scan_count == 0, "0"),
        (McpServerRegistry.scan_count == 1, "1"),
        (McpServerRegistry.scan_count.between(2, 3), "2-3"),
        (McpServerRegistry.scan_count.between(4, 9), "4-9"),
        else_="10+",
    )


def scan_count_histogram(db: Session) -> Dict[str, Any]:
    """Compute the registry scan-count bands and scan freshness counts."""
    band_expression = _scan_count_band()

    bucket_rows = db.execute(
        select(band_expression.label("band"), func.count().label("count"))
        .group_by(band_expression)
    ).all()
    counts = {band: 0 for band in _BANDS}
    for band, count in bucket_rows:
        counts[str(band)] = int(count)

    total_servers = int(
        db.execute(
            select(func.count(McpServerRegistry.server_id))
        ).scalar_one()
    )
    never_scanned = int(
        db.execute(
            select(
                func.sum(
                    case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)
                )
            )
        ).scalar_one()
        or 0
    )

    tier_rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    tier_mix = [
        {"risk_tier": tier or "unknown", "count": int(count)}
        for tier, count in tier_rows
    ]

    return {
        "total_servers": total_servers,
        "buckets": [{"band": band, "count": counts[band]} for band in _BANDS],
        "never_scanned": never_scanned,
        "scanned": total_servers - never_scanned,
        "tier_mix": tier_mix,
    }


def get_scan_count_histogram(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return the scan-count histogram using the application session."""
    return scan_count_histogram(db)


def compute_scan_count_histogram(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation naming convention."""
    return scan_count_histogram(db)


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
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    seeded = [
        ("server-0", "Server 0", 0, None, "low"),
        ("server-1", "Server 1", 1, now, "medium"),
        ("server-2", "Server 2", 2, now, "high"),
        ("server-3", "Server 3", 5, now, "critical"),
        ("server-4", "Server 4", 11, now, "low"),
        ("server-5", "Server 5", 1, now, "high"),
    ]

    with SessionLocal() as session:
        for server_id, name, scan_count, last_scanned, risk_tier in seeded:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    scan_count=scan_count,
                    last_scanned=last_scanned,
                    risk_tier=risk_tier,
                )
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/scan-count-histogram")
    def registry_scan_count_histogram(db: Session = Depends(get_session)):
        return scan_count_histogram(db)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-count-histogram")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 6, payload
    assert payload["never_scanned"] == 1, payload
    assert payload["scanned"] == 5, payload
    assert {item["band"]: item["count"] for item in payload["buckets"]} == {
        "0": 1,
        "1": 2,
        "2-3": 1,
        "4-9": 1,
        "10+": 1,
    }, payload
    assert sum(item["count"] for item in payload["tier_mix"]) == 6, payload
    print("PASS")