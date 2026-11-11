from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry"])


class ScanFreshnessBucket(BaseModel):
    label: str
    count: int
    pct: float


class ScanFreshnessHistogramResponse(BaseModel):
    days: int
    generated_at: datetime
    total_servers: int
    buckets: List[ScanFreshnessBucket]
    never_scanned: int


BUCKET_LABELS = (
    "0-1d",
    "2-7d",
    "8-30d",
    "31-90d",
    ">90d",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: float) -> str:
    if age_days <= 1:
        return "0-1d"
    if age_days <= 7:
        return "2-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return ">90d"


def build_scan_freshness_histogram(
    session,
    days: int = 30,
    generated_at: datetime | None = None,
) -> ScanFreshnessHistogramResponse:
    clamped_days = max(1, min(int(days), 365))
    now = _as_utc(generated_at or datetime.now(timezone.utc))

    rows = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.last_scanned,
        McpServerRegistry.last_seen,
        McpServerRegistry.first_seen,
        McpServerRegistry.scan_count,
    ).all()

    counts = {label: 0 for label in BUCKET_LABELS}
    never_scanned = 0

    for row in rows:
        last_scanned = row.last_scanned
        if last_scanned is None:
            never_scanned += 1
            continue

        scanned_at = _as_utc(last_scanned)
        age_days = max(0.0, (now - scanned_at).total_seconds() / 86400.0)
        counts[_bucket_for_age(age_days)] += 1

    total_servers = len(rows)
    buckets = [
        ScanFreshnessBucket(
            label=label,
            count=counts[label],
            pct=round((counts[label] / total_servers) * 100.0, 2)
            if total_servers
            else 0.0,
        )
        for label in BUCKET_LABELS
    ]

    return ScanFreshnessHistogramResponse(
        days=clamped_days,
        generated_at=now,
        total_servers=total_servers,
        buckets=buckets,
        never_scanned=never_scanned,
    )


@router.get(
    "/registry/scan-freshness-histogram",
    response_model=ScanFreshnessHistogramResponse,
)
def get_scan_freshness_histogram(
    days: int = Query(default=30),
    session=Depends(get_session),
) -> ScanFreshnessHistogramResponse:
    return build_scan_freshness_histogram(session=session, days=days)


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
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    seeds = (
        ("00000000-0000-0000-0000-000000000001", now - timedelta(days=0), 1),
        ("00000000-0000-0000-0000-000000000002", now - timedelta(days=3), 1),
        ("00000000-0000-0000-0000-000000000003", now - timedelta(days=40), 1),
        ("00000000-0000-0000-0000-000000000004", now - timedelta(days=200), 1),
        ("00000000-0000-0000-0000-000000000005", None, 0),
    )

    with SessionLocal() as session:
        for server_id, last_scanned, scan_count in seeds:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    url=f"https://{server_id}.example",
                    first_seen=now - timedelta(days=300),
                    last_seen=now,
                    last_scanned=last_scanned,
                    scan_count=scan_count,
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-freshness-histogram?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 5
        assert sum(bucket["count"] for bucket in payload["buckets"]) + payload[
            "never_scanned"
        ] == 5
        counts = {bucket["label"]: bucket["count"] for bucket in payload["buckets"]}
        assert counts["2-7d"] == 1

    engine.dispose()
    print("PASS")