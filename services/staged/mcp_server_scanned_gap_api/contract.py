from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

BUCKET_NAMES = ("0-1", "2-7", "8-30", "31-90", "90+", "NULL/never-scanned")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> int:
    if value is None:
        return 0
    elapsed = now - _as_utc(value)
    if elapsed.total_seconds() <= 0:
        return 0
    return int(elapsed.total_seconds() // 86400)


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


@router.get("/registry/scan-gap")
def get_registry_scan_gap(
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
            McpServerRegistry.first_seen,
            McpServerRegistry.scan_count,
        )
    ).all()

    counts = {bucket: 0 for bucket in BUCKET_NAMES}
    stale_top = []

    for row in rows:
        never_scanned = row.last_scanned is None
        age_days = _age_days(row.last_scanned or row.first_seen, now)
        bucket = "NULL/never-scanned" if never_scanned else _bucket_for_age(age_days)
        counts[bucket] += 1

        if never_scanned or age_days >= days:
            stale_top.append(
                {
                    "server_id": row.server_id,
                    "name": row.name,
                    "age_days": age_days,
                    "scan_count": row.scan_count,
                    "risk_tier": row.risk_tier,
                }
            )

    stale_top.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "as_of": now.isoformat(),
        "days": days,
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in BUCKET_NAMES
        ],
        "total_servers": len(rows),
        "never_scanned": counts["NULL/never-scanned"],
        "stale_top": stale_top[:20],
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    session = TestSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never Scanned",
                    risk_tier="high",
                    first_seen=now - timedelta(days=10),
                    last_scanned=None,
                    last_seen=now,
                    scan_count=0,
                    registry_source="self-test",
                ),
                McpServerRegistry(
                    server_id="two-days-old",
                    name="Two Days Old",
                    risk_tier="medium",
                    first_seen=now - timedelta(days=400),
                    last_scanned=now - timedelta(days=2),
                    last_seen=now,
                    scan_count=2,
                    registry_source="self-test",
                ),
                McpServerRegistry(
                    server_id="two-hundred-days-old",
                    name="Two Hundred Days Old",
                    risk_tier="critical",
                    first_seen=now - timedelta(days=500),
                    last_scanned=now - timedelta(days=200),
                    last_seen=now,
                    scan_count=1,
                    registry_source="self-test",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session() -> Iterator[Session]:
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-gap")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 3, payload
    assert sum(item["count"] for item in payload["buckets"]) == payload["total_servers"], payload
    assert payload["never_scanned"] == 1, payload
    assert payload["stale_top"], payload
    assert payload["stale_top"][0]["server_id"] == "two-hundred-days-old", payload
    assert payload["days"] == 30, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()