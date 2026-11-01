from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, literal, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api")


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported timestamp value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return float(ordered[index])


@router.get("/disputes/resolution-lag")
def resolution_lag(
    days: int = Query(30, ge=1, le=3650),
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    as_of = datetime.now(timezone.utc)
    cutoff = as_of - timedelta(days=days)
    status_filter = bindparam("status_filter")

    statement = (
        select(
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
        .where(
            McpScoreDispute.created_at >= cutoff,
            literal(days).between(1, 3650),
            or_(status_filter.is_(None), McpScoreDispute.status == status_filter),
        )
        .order_by(McpScoreDispute.id)
    )

    rows = session.execute(
        statement,
        {"status_filter": status},
    ).all()

    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "unresolved": 0, "durations": []}
    )

    for status_value, created_at, resolved_at in rows:
        bucket_name = str(status_value) if status_value is not None else "unknown"
        bucket = buckets[bucket_name]
        bucket["count"] += 1

        if created_at is None:
            continue

        created = _as_utc(created_at)
        if resolved_at is None:
            bucket["unresolved"] += 1
            duration = max(0.0, (as_of - created).total_seconds() / 3600.0)
        else:
            duration = max(0.0, (_as_utc(resolved_at) - created).total_seconds() / 3600.0)

        bucket["durations"].append(duration)

    by_status = []
    for bucket_name in sorted(buckets):
        bucket = buckets[bucket_name]
        durations = bucket["durations"]
        by_status.append(
            {
                "status": bucket_name,
                "count": bucket["count"],
                "avg_lag_hours": (
                    float(sum(durations) / len(durations)) if durations else None
                ),
                "p90_lag_hours": _percentile(durations, 0.90),
                "unresolved": bucket["unresolved"],
            }
        )

    return {
        "days": days,
        "as_of": as_of.isoformat(),
        "by_status": by_status,
        "total": sum(item["count"] for item in by_status),
    }


def _seed_sqlite(session) -> None:
    session.execute(
        __import__("sqlalchemy").text(
            """
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR,
                submitted_by VARCHAR,
                proposed_overall_risk VARCHAR,
                proposed_axes TEXT,
                reason_category VARCHAR,
                explanation TEXT,
                status VARCHAR NOT NULL,
                admin_note TEXT,
                created_at DATETIME NOT NULL,
                resolved_at DATETIME
            )
            """
        )
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        (1, 10.0),
        (2, 10.0),
        (3, 40.0),
        (4, 40.0),
    ]
    for row_id, lag_hours in rows:
        created = now - timedelta(hours=lag_hours)
        resolved = created + timedelta(hours=lag_hours)
        session.execute(
            __import__("sqlalchemy").text(
                """
                INSERT INTO mcp_score_disputes (
                    id, server_id, submitted_by, proposed_overall_risk,
                    proposed_axes, reason_category, explanation, status,
                    admin_note, created_at, resolved_at
                ) VALUES (
                    :id, :server_id, :submitted_by, :proposed_overall_risk,
                    :proposed_axes, :reason_category, :explanation, :status,
                    :admin_note, :created_at, :resolved_at
                )
                """
            ),
            {
                "id": row_id,
                "server_id": f"server-{row_id}",
                "submitted_by": "acceptance-test",
                "proposed_overall_risk": "0.5",
                "proposed_axes": "[]",
                "reason_category": "test",
                "explanation": "acceptance-test",
                "status": "resolved",
                "admin_note": None,
                "created_at": created,
                "resolved_at": resolved,
            },
        )

    open_created = now - timedelta(hours=5)
    session.execute(
        __import__("sqlalchemy").text(
            """
            INSERT INTO mcp_score_disputes (
                id, server_id, submitted_by, proposed_overall_risk,
                proposed_axes, reason_category, explanation, status,
                admin_note, created_at, resolved_at
            ) VALUES (
                :id, :server_id, :submitted_by, :proposed_overall_risk,
                :proposed_axes, :reason_category, :explanation, :status,
                :admin_note, :created_at, :resolved_at
            )
            """
        ),
        {
            "id": 5,
            "server_id": "server-5",
            "submitted_by": "acceptance-test",
            "proposed_overall_risk": "0.5",
            "proposed_axes": "[]",
            "reason_category": "test",
            "explanation": "acceptance-test",
            "status": "open",
            "admin_note": None,
            "created_at": open_created,
            "resolved_at": None,
        },
    )


def main() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        _seed_sqlite(connection)

    def override_get_session():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/disputes/resolution-lag", params={"days": 30})
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 30
            assert payload["total"] == 5
            assert sum(item["count"] for item in payload["by_status"]) == 5

            resolved = next(
                item for item in payload["by_status"] if item["status"] == "resolved"
            )
            assert resolved["count"] == 4
            assert resolved["unresolved"] == 0
            assert abs(resolved["avg_lag_hours"] - 25.0) <= 1.0
            assert resolved["p90_lag_hours"] >= 40.0

            unresolved = next(
                item for item in payload["by_status"] if item["status"] == "open"
            )
            assert unresolved["count"] == 1
            assert unresolved["unresolved"] == 1
            assert unresolved["avg_lag_hours"] is not None
            assert unresolved["p90_lag_hours"] is not None
            assert unresolved["avg_lag_hours"] >= 5.0
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()