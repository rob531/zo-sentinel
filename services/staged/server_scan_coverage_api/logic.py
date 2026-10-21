from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceScanCoverage(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class ScanCoverageResponse(BaseModel):
    total: int
    never_scanned: int
    stale: int
    fresh: int
    coverage_pct: float
    by_source: list[SourceScanCoverage]
    generated_at: datetime


router = APIRouter()
scan_coverage_router = router


def get_scan_coverage(
    db: Session,
    max_age_days: int = 30,
) -> dict[str, Any]:
    if max_age_days < 1 or max_age_days > 365:
        raise ValueError("max_age_days must be between 1 and 365")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max_age_days
    )
    age_days = literal(max_age_days)

    never_scanned = case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)
    stale = case(
        (
            and_(
                McpServerRegistry.last_scanned.is_not(None),
                McpServerRegistry.last_scanned < cutoff,
                age_days.between(1, 365),
            ),
            1,
        ),
        else_=0,
    )
    source = func.coalesce(McpServerRegistry.registry_source, "unknown")

    rows = db.execute(
        select(
            source.label("registry_source"),
            func.count().label("total"),
            func.sum(never_scanned).label("never_scanned"),
            func.sum(stale).label("stale"),
            func.avg(func.coalesce(McpServerRegistry.scan_count, 0)).label(
                "avg_scan_count"
            ),
        )
        .select_from(McpServerRegistry)
        .group_by(source)
        .order_by(source)
    ).all()

    by_source = [
        {
            "registry_source": str(row.registry_source),
            "total": int(row.total or 0),
            "never_scanned": int(row.never_scanned or 0),
            "stale": int(row.stale or 0),
            "avg_scan_count": float(row.avg_scan_count or 0.0),
        }
        for row in rows
    ]

    total = sum(item["total"] for item in by_source)
    never_count = sum(item["never_scanned"] for item in by_source)
    stale_count = sum(item["stale"] for item in by_source)
    fresh_count = total - never_count - stale_count
    coverage_pct = ((total - never_count) / total * 100.0) if total else 0.0

    return {
        "total": total,
        "never_scanned": never_count,
        "stale": stale_count,
        "fresh": fresh_count,
        "coverage_pct": float(coverage_pct),
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


compute_scan_coverage = get_scan_coverage
calculate_scan_coverage = get_scan_coverage
get_scan_coverage_report = get_scan_coverage


@router.get(
    "/registry/scan-coverage",
    response_model=ScanCoverageResponse,
)
def scan_coverage_endpoint(
    max_age_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ScanCoverageResponse:
    return ScanCoverageResponse(**get_scan_coverage(db, max_age_days))


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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="scan-1",
                name="Never scanned",
                registry_source="alpha",
                risk_tier="low",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="scan-2",
                name="Stale",
                registry_source="alpha",
                risk_tier="medium",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now - timedelta(days=45),
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="scan-3",
                name="Recent one",
                registry_source="alpha",
                risk_tier="low",
                first_seen=now - timedelta(days=20),
                last_seen=now,
                last_scanned=now - timedelta(days=1),
                scan_count=3,
            ),
            McpServerRegistry(
                server_id="scan-4",
                name="Recent two",
                registry_source="beta",
                risk_tier="medium",
                first_seen=now - timedelta(days=20),
                last_seen=now,
                last_scanned=now - timedelta(days=2),
                scan_count=4,
            ),
            McpServerRegistry(
                server_id="scan-5",
                name="Recent three",
                registry_source="beta",
                risk_tier="high",
                first_seen=now - timedelta(days=20),
                last_seen=now,
                last_scanned=now - timedelta(days=3),
                scan_count=5,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/scan-coverage",
        params={"max_age_days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["never_scanned"] == 1
    assert payload["stale"] == 1
    assert payload["fresh"] == 3
    assert payload["coverage_pct"] == 80.0
    assert sum(item["total"] for item in payload["by_source"]) == payload["total"]

    test_session.close()
    test_engine.dispose()
    print("PASS")