from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_ORDER = ("fresh", "recent", "week", "month", "stale", "never")


def get_scan_freshness_buckets(
    session: Session = Depends(get_session),
    buckets: int = 6,
    risk_tier: Optional[str] = None,
) -> dict:
    registry = McpServerRegistry
    now = datetime.utcnow()

    one_hour = now - timedelta(hours=1)
    one_day = now - timedelta(hours=24)
    one_week = now - timedelta(hours=168)
    one_month = now - timedelta(hours=720)

    bucket_expression = case(
        (registry.last_scanned.is_(None), "never"),
        (registry.last_scanned > one_hour, "fresh"),
        (registry.last_scanned > one_day, "recent"),
        (registry.last_scanned > one_week, "week"),
        (registry.last_scanned > one_month, "month"),
        else_="stale",
    )

    filters = []
    if risk_tier is not None:
        filters.append(registry.risk_tier == risk_tier)

    count_statement = select(
        bucket_expression.label("bucket"),
        func.count(registry.server_id).label("servers"),
    )
    if filters:
        count_statement = count_statement.where(*filters)
    count_statement = count_statement.group_by(bucket_expression)

    counts = {name: 0 for name in _BUCKET_ORDER}
    for row in session.execute(count_statement):
        counts[row.bucket] = int(row.servers)

    total_statement = select(func.count(registry.server_id))
    if filters:
        total_statement = total_statement.where(*filters)
    total_servers = int(session.execute(total_statement).scalar_one())

    average_statement = select(func.avg(registry.scan_count))
    if filters:
        average_statement = average_statement.where(*filters)
    average_scan_count = session.execute(average_statement).scalar()

    lag_statement = select(
        func.count(registry.server_id).filter(
            registry.last_seen.is_not(None)
            & registry.last_scanned.is_not(None)
            & (registry.last_seen > registry.last_scanned)
        )
    )
    if filters:
        lag_statement = lag_statement.where(*filters)
    rescan_lag_servers = int(session.execute(lag_statement).scalar_one())

    stale_sample_statement = (
        select(registry.server_id)
        .where(
            registry.last_scanned.is_not(None)
            & (registry.last_scanned <= one_month)
        )
        .order_by(registry.last_scanned.asc())
        .limit(20)
    )
    if filters:
        stale_sample_statement = stale_sample_statement.where(*filters)
    stale_sample = [
        str(server_id)
        for server_id in session.execute(stale_sample_statement).scalars()
        if server_id is not None
    ]

    bucket_payload = [
        {
            "bucket": name,
            "servers": counts[name],
            "pct": round((counts[name] / total_servers * 100.0), 2)
            if total_servers
            else 0.0,
        }
        for name in _BUCKET_ORDER
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "buckets": bucket_payload,
        "total_servers": total_servers,
        "avg_scan_count": float(average_scan_count)
        if average_scan_count is not None
        else None,
        "rescan_lag_servers": rescan_lag_servers,
        "stale_sample": stale_sample,
    }


get_scan_freshness = get_scan_freshness_buckets
get_freshness_buckets = get_scan_freshness_buckets


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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    test_now = datetime.utcnow()
    with TestingSessionLocal() as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    last_scanned=test_now - timedelta(minutes=30),
                    last_seen=test_now,
                    scan_count=10,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="recent-server",
                    last_scanned=test_now - timedelta(hours=5),
                    last_seen=test_now - timedelta(hours=4),
                    scan_count=6,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="week-server",
                    last_scanned=test_now - timedelta(hours=72),
                    last_seen=test_now - timedelta(hours=71),
                    scan_count=4,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    last_scanned=test_now - timedelta(hours=1000),
                    last_seen=test_now - timedelta(hours=900),
                    scan_count=2,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="never-server",
                    last_scanned=None,
                    last_seen=None,
                    scan_count=0,
                    risk_tier="low",
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestingSessionLocal() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-freshness")
    def scan_freshness(
        session: Session = Depends(get_session),
        buckets: int = 6,
    ):
        return get_scan_freshness_buckets(session=session, buckets=buckets)

    response = TestClient(test_app).get(
        "/api/registry/scan-freshness",
        params={"buckets": 6},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 5
    assert sum(item["servers"] for item in payload["buckets"]) == payload["total_servers"]
    assert next(
        item for item in payload["buckets"] if item["bucket"] == "never"
    )["servers"] == 1
    assert payload["avg_scan_count"] is not None
    print("PASS")