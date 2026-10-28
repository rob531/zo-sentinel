from datetime import datetime, timezone
from statistics import median

from fastapi import APIRouter, Depends
from sqlalchemy import func

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api")


def _utc_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/disputes/rollup")
def dispute_rollup(db=Depends(get_session)) -> dict:
    total = (
        db.query(func.count(McpScoreDispute.id))
        .scalar()
        or 0
    )

    status_counts = (
        db.query(
            McpScoreDispute.status,
            func.count(McpScoreDispute.id),
        )
        .group_by(McpScoreDispute.status)
        .all()
    )
    reason_counts = (
        db.query(
            McpScoreDispute.reason_category,
            func.count(McpScoreDispute.id),
        )
        .group_by(McpScoreDispute.reason_category)
        .all()
    )

    open_count = (
        db.query(func.count(McpScoreDispute.id))
        .filter(McpScoreDispute.resolved_at.is_(None))
        .scalar()
        or 0
    )

    dispute_rows = (
        db.query(
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
        .all()
    )

    resolution_days = []
    open_created_at = []
    now = datetime.now(timezone.utc)

    for created_at, resolved_at in dispute_rows:
        if resolved_at is not None and created_at is not None:
            created = _utc_datetime(created_at)
            resolved = _utc_datetime(resolved_at)
            resolution_days.append(
                max(0.0, (resolved - created).total_seconds() / 86400.0)
            )
        elif resolved_at is None and created_at is not None:
            open_created_at.append(_utc_datetime(created_at))

    median_days_to_resolve = (
        float(median(resolution_days)) if resolution_days else None
    )
    oldest_open_days = (
        int(max(0.0, (now - created).total_seconds() // 86400.0))
        if open_created_at
        else None
    )

    by_status = {
        status: count
        for status, count in sorted(
            status_counts,
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    }
    by_reason_category = {
        reason_category: count
        for reason_category, count in sorted(
            reason_counts,
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    }

    return {
        "total": int(total),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "open_count": int(open_count),
        "median_days_to_resolve": median_days_to_resolve,
        "oldest_open_days": oldest_open_days,
    }


def _self_test():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    reason_category TEXT,
                    created_at DATETIME,
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, status, reason_category, created_at, resolved_at)
                VALUES
                    (1, 'open', 'risk', '2025-01-01T00:00:00Z', NULL),
                    (2, 'resolved', 'quality', '2025-01-01T00:00:00Z',
                        '2025-01-03T00:00:00Z'),
                    (3, 'pending', 'quality', '2025-01-02T00:00:00Z', NULL),
                    (4, 'resolved', 'risk', '2025-01-02T00:00:00Z',
                        '2025-01-04T00:00:00Z'),
                    (5, 'resolved', 'risk', '2025-01-03T00:00:00Z',
                        '2025-01-04T00:00:00Z')
                """
            )
        )

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/rollup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["open_count"] == 2
    assert sum(payload["by_status"].values()) == 5
    assert sum(payload["by_reason_category"].values()) == 5
    assert payload["median_days_to_resolve"] is not None
    assert payload["oldest_open_days"] is not None
    print("PASS")


if __name__ == "__main__":
    _self_test()