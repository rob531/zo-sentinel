from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import bindparam, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

_BUCKETS = ("0-1", "2-7", "8-30", "31-90", "90+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 1:
        return "0-1"
    if age_days <= 7:
        return "2-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def build_last_seen_aging(session: Session, days: int = 365) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    now = _utc_now()
    cutoff = (now - timedelta(days=days)).replace(tzinfo=None)
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.last_seen,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
            McpServerRegistry.risk_tier,
        )
        .where(
            or_(
                McpServerRegistry.last_seen.is_(None),
                McpServerRegistry.last_seen >= bindparam("cutoff"),
            )
        )
        .order_by(McpServerRegistry.server_id)
    )
    rows = session.execute(statement, {"cutoff": cutoff}).all()

    counts = {bucket: 0 for bucket in _BUCKETS}
    critical_counts = {bucket: 0 for bucket in _BUCKETS}
    high_counts = {bucket: 0 for bucket in _BUCKETS}

    for row in rows:
        if row.last_seen is None:
            bucket = "90+"
        else:
            age_days = max(0, (now - _as_utc(row.last_seen)).days)
            bucket = _bucket_for_age(age_days)

        counts[bucket] += 1
        risk_tier = str(row.risk_tier or "").strip().lower()
        if risk_tier == "critical":
            critical_counts[bucket] += 1
        elif risk_tier == "high":
            high_counts[bucket] += 1

    buckets = [
        {
            "bucket": bucket,
            "count": counts[bucket],
            "critical_count": critical_counts[bucket],
            "high_count": high_counts[bucket],
        }
        for bucket in _BUCKETS
    ]

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "buckets": buckets,
        "total": sum(counts.values()),
    }


def get_last_seen_aging(
    session: Session = Depends(get_session),
    days: int = Query(365, ge=1, le=36500),
) -> dict[str, Any]:
    return build_last_seen_aging(session, days)


def get_registry_last_seen_aging(session: Session, days: int = 365) -> dict[str, Any]:
    return build_last_seen_aging(session, days)


@router.get("/registry/last-seen-aging")
def registry_last_seen_aging(
    session: Session = Depends(get_session),
    days: int = Query(365, ge=1, le=36500),
) -> dict[str, Any]:
    return build_last_seen_aging(session, days)


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

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    offsets = (0, 3, 10, 60, 200)
    risk_tiers = ("critical", "high", "medium", "low", "critical")

    with TestSession() as test_session:
        for index, (offset, risk_tier) in enumerate(zip(offsets, risk_tiers), start=1):
            last_seen = base_time - timedelta(days=offset)
            test_session.add(
                McpServerRegistry(
                    server_id=f"aging-server-{index}",
                    name=f"Aging Server {index}",
                    first_seen=last_seen - timedelta(days=10),
                    last_seen=last_seen,
                    last_scanned=last_seen,
                    scan_count=index,
                    risk_tier=risk_tier,
                )
            )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    response = TestClient(that_app).get(
        "/api/registry/last-seen-aging",
        params={"days": 365},
    )
    payload = response.json()

    assert response.status_code == 200
    assert "buckets" in payload
    assert payload["total"] == 5
    assert next(
        bucket["count"] for bucket in payload["buckets"] if bucket["bucket"] == "0-1"
    ) == 1

    print("PASS")