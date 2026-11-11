from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _coverage_row(
    registry_source: str | None,
    total_servers: int,
    scanned_servers: int,
    never_scanned: int,
    stale_scanned: int,
    avg_scan_count: float,
) -> dict[str, Any]:
    pct_covered = round((scanned_servers / total_servers) * 100, 2) if total_servers else 0.0
    return {
        "registry_source": registry_source,
        "total_servers": total_servers,
        "scanned_servers": scanned_servers,
        "never_scanned": never_scanned,
        "stale_scanned": stale_scanned,
        "avg_scan_count": round(float(avg_scan_count or 0.0), 2),
        "pct_covered": pct_covered,
    }


def get_scan_coverage(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return read-only registry scan coverage statistics."""
    days = max(1, min(365, int(days)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    scanned = McpServerRegistry.last_scanned.is_not(None)
    stale = scanned & (McpServerRegistry.last_scanned < cutoff)

    query = (
        select(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("total_servers"),
            func.sum(case((scanned, 1), else_=0)).label("scanned_servers"),
            func.sum(case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)).label(
                "never_scanned"
            ),
            func.sum(case((stale, 1), else_=0)).label("stale_scanned"),
            func.avg(McpServerRegistry.scan_count).label("avg_scan_count"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )

    rows = session.execute(query).all()
    sources = [
        _coverage_row(
            registry_source=row.registry_source,
            total_servers=int(row.total_servers or 0),
            scanned_servers=int(row.scanned_servers or 0),
            never_scanned=int(row.never_scanned or 0),
            stale_scanned=int(row.stale_scanned or 0),
            avg_scan_count=float(row.avg_scan_count or 0.0),
        )
        for row in rows
    ]

    total_servers = sum(source["total_servers"] for source in sources)
    scanned_servers = sum(source["scanned_servers"] for source in sources)
    never_scanned = sum(source["never_scanned"] for source in sources)
    stale_scanned = sum(source["stale_scanned"] for source in sources)
    weighted_scan_count = sum(
        source["avg_scan_count"] * source["total_servers"] for source in sources
    )
    avg_scan_count = weighted_scan_count / total_servers if total_servers else 0.0

    summary = _coverage_row(
        registry_source=None,
        total_servers=total_servers,
        scanned_servers=scanned_servers,
        never_scanned=never_scanned,
        stale_scanned=stale_scanned,
        avg_scan_count=avg_scan_count,
    )
    summary.pop("registry_source")

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "summary": summary,
    }


get_registry_scan_coverage = get_scan_coverage


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)

    test_session = Session(test_engine)
    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="recent",
                name="recent",
                url="https://recent.example",
                registry_source="alpha",
                last_scanned=now - timedelta(days=2),
                scan_count=4,
                last_seen=now,
                risk_tier="low",
                trust_score=0.9,
            ),
            McpServerRegistry(
                server_id="stale",
                name="stale",
                url="https://stale.example",
                registry_source="alpha",
                last_scanned=now - timedelta(days=60),
                scan_count=2,
                last_seen=now,
                risk_tier="medium",
                trust_score=0.6,
            ),
            McpServerRegistry(
                server_id="never",
                name="never",
                url="https://never.example",
                registry_source="beta",
                last_scanned=None,
                scan_count=0,
                last_seen=now,
                risk_tier="unknown",
                trust_score=0.0,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-coverage")
    def scan_coverage_endpoint(days: int = 30, session: Session = Depends(get_session)):
        return get_scan_coverage(days=days, session=session)

    response = TestClient(test_app).get(
        "/api/registry/scan-coverage", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_servers"] == 3
    assert payload["summary"]["never_scanned"] == 1
    assert 0 <= payload["summary"]["pct_covered"] <= 100
    assert len(payload["sources"]) == 2
    print("PASS")