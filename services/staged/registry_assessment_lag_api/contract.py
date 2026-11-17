from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


def _as_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _lag_seconds(
    last_seen: Optional[datetime],
    last_scanned: Optional[datetime],
    first_seen: Optional[datetime],
    last_assessed: Optional[datetime],
) -> Optional[float]:
    reference = last_seen or last_scanned or first_seen
    assessed = _as_utc_naive(last_assessed)
    reference = _as_utc_naive(reference)
    if reference is None or assessed is None:
        return None
    return (reference - assessed).total_seconds()


@router.get("/registry/assessment-lag")
def registry_assessment_lag(
    threshold_seconds: int = Query(default=86400),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_assessed,
            McpServerRegistry.scan_count,
            McpServerRegistry.risk_tier,
        )
    ).all()

    bucket_names = [
        "never_assessed",
        "lt_1h",
        "lt_1d",
        "lt_7d",
        "gte_7d",
    ]
    counts = {name: 0 for name in bucket_names}

    for row in rows:
        if row.last_assessed is None:
            counts["never_assessed"] += 1
            continue

        lag = _lag_seconds(
            row.last_seen,
            row.last_scanned,
            row.first_seen,
            row.last_assessed,
        )
        if lag is None or lag < 3600:
            counts["lt_1h"] += 1
        elif lag < 86400:
            counts["lt_1d"] += 1
        elif lag < 604800:
            counts["lt_7d"] += 1
        else:
            counts["gte_7d"] += 1

    return {
        "buckets": [
            {"bucket": name, "count": counts[name]}
            for name in bucket_names
        ],
        "total": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold_seconds": threshold_seconds,
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    last_assessed DATETIME,
                    scan_count INTEGER,
                    risk_tier VARCHAR
                )
                """
            )
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestingSession()
    try:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, first_seen, last_seen, last_scanned, last_assessed, scan_count, risk_tier)
                VALUES
                    (:server_id, :first_seen, :last_seen, :last_scanned, :last_assessed, :scan_count, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "never",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": None,
                    "scan_count": 1,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "ten-minutes",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now.replace(microsecond=0) - __import__("datetime").timedelta(minutes=10),
                    "scan_count": 2,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "three-days",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now - __import__("datetime").timedelta(days=3),
                    "scan_count": 3,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "thirty-days",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now - __import__("datetime").timedelta(days=30),
                    "scan_count": 4,
                    "risk_tier": "medium",
                },
            ],
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            yield session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/registry/assessment-lag")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 4, payload
            buckets = {item["bucket"]: item["count"] for item in payload["buckets"]}
            assert set(buckets) == {
                "never_assessed",
                "lt_1h",
                "lt_1d",
                "lt_7d",
                "gte_7d",
            }, buckets
            assert buckets["never_assessed"] == 1, buckets
            assert buckets["gte_7d"] == 1, buckets
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()