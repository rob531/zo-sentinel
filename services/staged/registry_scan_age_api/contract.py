from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = ("0-1", "2-7", "8-30", "31-90", "90+")


class ScanAgeBucket(BaseModel):
    bucket: Optional[str]
    count: int
    by_source: Dict[str, int]


class StaleServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    risk_tier: Optional[str]
    last_scanned: Optional[datetime]


class ScanAgeResponse(BaseModel):
    days: int
    generated_at: datetime
    buckets: List[ScanAgeBucket]
    stale_threshold_days: int
    stale_servers: List[StaleServer]


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _age_days(last_scanned: Optional[datetime], now: datetime) -> Optional[int]:
    if last_scanned is None:
        return None
    elapsed = now - _as_naive_utc(last_scanned)
    return max(0, elapsed.days)


def _bucket_for_age(age_days: Optional[int]) -> Optional[str]:
    if age_days is None:
        return None
    if age_days <= 1:
        return "0-1"
    if age_days <= 7:
        return "2-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def _is_stale(last_scanned: Optional[datetime], now: datetime, days: int) -> bool:
    if last_scanned is None:
        return False
    elapsed = now - _as_naive_utc(last_scanned)
    return elapsed > timedelta(days=days)


def build_scan_age_report(
    session,
    days: int,
    *,
    stale_only: bool = False,
) -> ScanAgeResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = session.execute(select(McpServerRegistry)).scalars().all()

    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    bucket_sources: Dict[Optional[str], Dict[str, int]] = {
        bucket: {} for bucket in list(BUCKETS) + [None]
    }
    stale_servers: List[StaleServer] = []

    for row in rows:
        age_days = _age_days(row.last_scanned, now)
        stale = _is_stale(row.last_scanned, now, days)

        if stale:
            stale_servers.append(
                StaleServer(
                    server_id=row.server_id,
                    name=row.name,
                    registry_source=row.registry_source,
                    risk_tier=row.risk_tier,
                    last_scanned=row.last_scanned,
                )
            )

        if stale_only and not stale:
            continue

        bucket = _bucket_for_age(age_days)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        source_counts = bucket_sources.setdefault(bucket, {})
        source = row.registry_source or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

    buckets = [
        ScanAgeBucket(
            bucket=bucket,
            count=bucket_counts.get(bucket, 0),
            by_source=bucket_sources.get(bucket, {}),
        )
        for bucket in list(BUCKETS) + [None]
    ]

    return ScanAgeResponse(
        days=days,
        generated_at=now,
        buckets=buckets,
        stale_threshold_days=days,
        stale_servers=stale_servers,
    )


router = APIRouter()


@router.get("/registry/scan-age", response_model=ScanAgeResponse)
def registry_scan_age(
    days: int = Query(30, ge=0),
    session=Depends(get_session),
) -> ScanAgeResponse:
    return build_scan_age_report(session, days)


@router.get("/registry/scan-age/stale", response_model=ScanAgeResponse)
def registry_scan_age_stale(
    days: int = Query(30, ge=0),
    session=Depends(get_session),
) -> ScanAgeResponse:
    return build_scan_age_report(session, days, stale_only=True)


app = FastAPI()
app.include_router(router, prefix="/api")


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(days=10)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-1",
                    name="Fresh Server",
                    registry_source="source-a",
                    risk_tier="low",
                    verdict="approved",
                    last_scanned=now,
                    scan_count=3,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="fresh-2",
                    name="Fresh Server 2",
                    registry_source="source-b",
                    risk_tier="medium",
                    verdict="approved",
                    last_scanned=now - timedelta(days=1),
                    scan_count=2,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="old-10d",
                    name="Ten Day Old Server",
                    registry_source="source-a",
                    risk_tier="high",
                    verdict="review",
                    last_scanned=old,
                    scan_count=1,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="older-45d",
                    name="Older Server",
                    registry_source="source-c",
                    risk_tier="high",
                    verdict="review",
                    last_scanned=now - timedelta(days=45),
                    scan_count=1,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="never-1",
                    name="Never Scanned Server",
                    registry_source="source-b",
                    risk_tier="medium",
                    verdict="unknown",
                    last_scanned=None,
                    scan_count=0,
                    last_seen=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/scan-age", params={"days": 7})
            assert response.status_code == 200, response.text
            payload = response.json()

            assert sum(item["count"] for item in payload["buckets"]) == 5
            assert any(
                item["bucket"] is None and item["count"] > 0
                for item in payload["buckets"]
            )
            assert any(
                server["server_id"] == "old-10d"
                for server in payload["stale_servers"]
            )

            stale_response = client.get(
                "/api/registry/scan-age/stale", params={"days": 7}
            )
            assert stale_response.status_code == 200, stale_response.text
            stale_payload = stale_response.json()
            assert all(
                server["server_id"] != "fresh-1"
                for server in stale_payload["stale_servers"]
            )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()