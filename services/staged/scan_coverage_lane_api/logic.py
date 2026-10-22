from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _coverage_result(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    try:
        stale_days = max(0, int(days))
    except (TypeError, ValueError):
        stale_days = 7

    cutoff = datetime.utcnow() - timedelta(days=stale_days)

    scanned_condition = McpServerRegistry.last_scanned.is_not(None)
    stale_condition = (
        McpServerRegistry.last_scanned.is_not(None)
        & (McpServerRegistry.last_scanned < cutoff)
    )
    normalized_scan_count = case(
        (McpServerRegistry.scan_count < 0, 0),
        else_=func.coalesce(McpServerRegistry.scan_count, 0),
    )

    statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("total_servers"),
            func.sum(
                case((scanned_condition, 1), else_=0)
            ).label("scanned_servers"),
            func.sum(
                case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)
            ).label("never_scanned"),
            func.sum(
                case((stale_condition, 1), else_=0)
            ).label("stale_servers"),
            func.avg(normalized_scan_count).label("avg_scan_count"),
        )
        .where(
            McpServerRegistry.registry_source == registry_source
            if registry_source is not None
            else True
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )

    rows = db.execute(statement).mappings().all()
    lanes: List[Dict[str, Any]] = []

    for row in rows:
        total = int(row["total_servers"] or 0)
        scanned = int(row["scanned_servers"] or 0)
        never_scanned = int(row["never_scanned"] or 0)
        stale = int(row["stale_servers"] or 0)
        average_scan_count = float(row["avg_scan_count"] or 0.0)
        coverage = round((scanned * 100.0 / total) if total else 0.0, 2)

        lanes.append(
            {
                "registry_source": row["registry_source"],
                "total_servers": total,
                "scanned_servers": scanned,
                "never_scanned": never_scanned,
                "stale_over_24h": stale,
                "avg_scan_count": round(average_scan_count, 2),
                "coverage_pct": coverage,
            }
        )

    total_servers = sum(lane["total_servers"] for lane in lanes)
    scanned_servers = sum(lane["scanned_servers"] for lane in lanes)
    overall_coverage = (
        round(scanned_servers * 100.0 / total_servers, 2)
        if total_servers
        else 0.0
    )

    return {
        "generated_at": datetime.now(timezone.utc),
        "total_servers": total_servers,
        "overall_coverage_pct": overall_coverage,
        "lanes": lanes,
    }


def get_coverage(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    return _coverage_result(db, registry_source=registry_source, days=days)


def get_scan_coverage(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    return _coverage_result(db, registry_source=registry_source, days=days)


def get_coverage_data(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    return _coverage_result(db, registry_source=registry_source, days=days)


def get_coverage_lanes(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> List[Dict[str, Any]]:
    return _coverage_result(db, registry_source=registry_source, days=days)["lanes"]


def calculate_coverage(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    return _coverage_result(db, registry_source=registry_source, days=days)


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        now = datetime.utcnow()
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    registry_source="alpha",
                    last_scanned=now,
                    scan_count=2,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    registry_source="alpha",
                    last_scanned=now,
                    scan_count=4,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    registry_source="beta",
                    last_scanned=None,
                    scan_count=0,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="server-4",
                    registry_source="beta",
                    last_scanned=None,
                    scan_count=-1,
                    last_seen=now,
                ),
            ]
        )
        session.commit()

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/scan/coverage")
    def coverage_route(
        registry_source: Optional[str] = None,
        days: int = 7,
        db: Session = Depends(get_session),
    ):
        return get_coverage(db, registry_source=registry_source, days=days)

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/scan/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["lanes"]) == 2
    assert payload["total_servers"] == 4
    never_scanned_lane = next(
        lane for lane in payload["lanes"] if lane["registry_source"] == "beta"
    )
    assert never_scanned_lane["never_scanned"] == 2
    assert never_scanned_lane["coverage_pct"] == 0.0

    print("PASS")