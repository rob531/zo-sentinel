from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


__all__ = [
    "compute_seen_to_scanned_lag",
    "get_lag_data",
    "get_registry_lag",
    "get_registry_seen_to_scanned_lag",
    "get_seen_to_scanned_lag",
]


def _calendar_day_lag(last_seen: Any, last_scanned: Any) -> int:
    """Return the non-negative number of calendar days between two values."""
    if last_seen is None or last_scanned is None:
        return 0

    if isinstance(last_seen, datetime):
        seen_date = last_seen.date()
    elif isinstance(last_seen, date):
        seen_date = last_seen
    else:
        seen_date = datetime.fromisoformat(str(last_seen)).date()

    if isinstance(last_scanned, datetime):
        scanned_date = last_scanned.date()
    elif isinstance(last_scanned, date):
        scanned_date = last_scanned
    else:
        scanned_date = datetime.fromisoformat(str(last_scanned)).date()

    return max(0, (seen_date - scanned_date).days)


def _bucket_for_lag(lag_days: int) -> str:
    if lag_days <= 1:
        return "0-1d"
    if lag_days <= 3:
        return "2-3d"
    if lag_days <= 7:
        return "4-7d"
    if lag_days <= 14:
        return "8-14d"
    if lag_days <= 30:
        return "15-30d"
    return "31+d"


def _compute_lag(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    cutoff_date = date.today() - timedelta(days=max(0, int(days)))

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
        .where(func.date(McpServerRegistry.last_seen) >= cutoff_date)
        .order_by(McpServerRegistry.server_id)
    )

    rows = session.execute(statement).all()

    never_scanned = 0
    stale_scanned = 0
    bucket_counts = {
        "0-1d": 0,
        "2-3d": 0,
        "4-7d": 0,
        "8-14d": 0,
        "15-30d": 0,
        "31+d": 0,
    }
    lag_rows: List[Dict[str, Any]] = []

    for server_id, first_seen, last_seen, last_scanned, scan_count in rows:
        if last_scanned is None:
            never_scanned += 1
            continue

        lag_days = _calendar_day_lag(last_seen, last_scanned)
        bucket_counts[_bucket_for_lag(lag_days)] += 1

        if lag_days > 1:
            stale_scanned += 1

        if server_id is not None:
            lag_rows.append(
                {
                    "server_id": str(server_id),
                    "lag_days": int(lag_days),
                }
            )

    lag_rows.sort(key=lambda row: (-row["lag_days"], row["server_id"]))

    return {
        "days": int(days),
        "never_scanned": int(never_scanned),
        "stale_scanned": int(stale_scanned),
        "buckets": [
            {"range": bucket_range, "count": int(count)}
            for bucket_range, count in bucket_counts.items()
        ],
        "worst": lag_rows[:10],
    }


def get_registry_seen_to_scanned_lag(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    """Compute registry seen-to-scanned lag metrics from the application database."""
    return _compute_lag(session=session, days=days)


def get_seen_to_scanned_lag(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    return _compute_lag(session=session, days=days)


def get_lag_data(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    return _compute_lag(session=session, days=days)


def get_registry_lag(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    return _compute_lag(session=session, days=days)


def compute_seen_to_scanned_lag(
    session: Session,
    days: int = 30,
) -> Dict[str, Any]:
    return _compute_lag(session=session, days=days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as test_session:
        test_session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    scan_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )

        now = datetime.utcnow()
        test_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, first_seen, last_seen, last_scanned, scan_count)
                VALUES
                    (:server_id, :first_seen, :last_seen, :last_scanned, :scan_count)
                """
            ),
            [
                {
                    "server_id": "never-scanned",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": None,
                    "scan_count": 0,
                },
                {
                    "server_id": "scanned-today",
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "scan_count": 1,
                },
                {
                    "server_id": "scanned-five-days-ago",
                    "first_seen": now - timedelta(days=5),
                    "last_seen": now,
                    "last_scanned": now - timedelta(days=5),
                    "scan_count": 1,
                },
            ],
        )
        test_session.commit()

        test_app = FastAPI()

        @test_app.get("/api/registry/seen-to-scanned-lag")
        def seen_to_scanned_lag(
            days: int = 30,
            session: Session = Depends(get_session),
        ) -> Dict[str, Any]:
            return get_registry_seen_to_scanned_lag(session=session, days=days)

        def override_get_session():
            yield test_session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/registry/seen-to-scanned-lag",
                params={"days": 30},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert payload["never_scanned"] == 1
        assert payload["worst"][0]["server_id"] == "scanned-five-days-ago"
        assert payload["worst"][0]["lag_days"] == 5
        assert payload["stale_scanned"] == 1

    test_engine.dispose()
    print("PASS")