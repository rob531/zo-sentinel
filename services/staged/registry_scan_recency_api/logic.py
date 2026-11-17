from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class SourceBucket(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class ScanRecencyResponse(BaseModel):
    days: int
    total_servers: int
    never_scanned: int
    stale_scanned: int
    coverage_pct: float
    by_source: List[SourceBucket]
    by_risk_tier: Dict[str, int]
    buckets: List[Dict[str, Any]]


BUCKET_NAMES = ["never", "<=1d", "1-7d", "7-30d", "30-90d", ">90d"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> Optional[float]:
    value = _as_utc(value)
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds() / 86400.0)


def compute_scan_recency(session: Session, days: int = 14) -> Dict[str, Any]:
    now = _utc_now()
    statement = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
    )
    rows = session.execute(statement).all()

    total_servers = len(rows)
    never_scanned = 0
    stale_scanned = 0
    fresh_scanned = 0
    bucket_counts = {name: 0 for name in BUCKET_NAMES}
    source_data: Dict[str, Dict[str, Any]] = {}
    risk_tier_counts: Dict[str, int] = {}

    for registry_source, risk_tier, last_scanned, scan_count in rows:
        source = registry_source if registry_source is not None else "unknown"
        tier = risk_tier if risk_tier is not None else "unknown"
        count = int(scan_count or 0)
        age = _age_days(last_scanned, now)

        if age is None:
            never_scanned += 1
            bucket_counts["never"] += 1
            is_stale = False
        else:
            if age > days:
                stale_scanned += 1
                is_stale = True
            else:
                fresh_scanned += 1
                is_stale = False

            if age <= 1:
                bucket_counts["<=1d"] += 1
            elif age <= 7:
                bucket_counts["1-7d"] += 1
            elif age <= 30:
                bucket_counts["7-30d"] += 1
            elif age <= 90:
                bucket_counts["30-90d"] += 1
            else:
                bucket_counts[">90d"] += 1

        source_bucket = source_data.setdefault(
            source,
            {
                "registry_source": source,
                "total": 0,
                "never_scanned": 0,
                "stale": 0,
                "scan_count_total": 0,
            },
        )
        source_bucket["total"] += 1
        source_bucket["scan_count_total"] += count
        if age is None:
            source_bucket["never_scanned"] += 1
        if is_stale:
            source_bucket["stale"] += 1

        risk_tier_counts[tier] = risk_tier_counts.get(tier, 0) + 1

    by_source = []
    for source in sorted(source_data):
        bucket = source_data[source]
        total = bucket["total"]
        by_source.append(
            {
                "registry_source": bucket["registry_source"],
                "total": total,
                "never_scanned": bucket["never_scanned"],
                "stale": bucket["stale"],
                "avg_scan_count": (
                    bucket["scan_count_total"] / total if total else 0.0
                ),
            }
        )

    coverage_pct = (
        (fresh_scanned / total_servers) * 100.0 if total_servers else 0.0
    )

    return {
        "days": days,
        "total_servers": total_servers,
        "never_scanned": never_scanned,
        "stale_scanned": stale_scanned,
        "coverage_pct": coverage_pct,
        "by_source": by_source,
        "by_risk_tier": risk_tier_counts,
        "buckets": [
            {"bucket": name, "count": bucket_counts[name]}
            for name in BUCKET_NAMES
        ],
    }


def get_scan_recency(session: Session, days: int = 14) -> Dict[str, Any]:
    return compute_scan_recency(session, days)


@router.get(
    "/registry/scan-recency",
    response_model=ScanRecencyResponse,
    summary="Registry scan recency",
)
def scan_recency(
    days: int = Query(14, ge=0),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_scan_recency(session, days)


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
    test_session = TestSession()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="selftest-never",
                name="never scanned",
                registry_source="selftest",
                risk_tier="tier-1",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=None,
                scan_count=0,
                trust_score=0.5,
                confidence=0.5,
            ),
            McpServerRegistry(
                server_id="selftest-stale",
                name="stale server",
                registry_source="selftest",
                risk_tier="tier-2",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now - timedelta(days=60),
                scan_count=1,
                trust_score=0.5,
                confidence=0.5,
            ),
            McpServerRegistry(
                server_id="selftest-fresh-1",
                name="fresh server one",
                registry_source="selftest",
                risk_tier="tier-3",
                first_seen=now - timedelta(days=10),
                last_seen=now,
                last_scanned=now - timedelta(days=2),
                scan_count=2,
                trust_score=0.5,
                confidence=0.5,
            ),
            McpServerRegistry(
                server_id="selftest-fresh-2",
                name="fresh server two",
                registry_source="selftest",
                risk_tier="tier-3",
                first_seen=now - timedelta(days=10),
                last_seen=now,
                last_scanned=now - timedelta(days=3),
                scan_count=3,
                trust_score=0.5,
                confidence=0.5,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-recency")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["never_scanned"] == 1
        assert payload["stale_scanned"] == 1
        assert payload["coverage_pct"] == 50.0

    test_session.close()
    test_engine.dispose()
    print("PASS")