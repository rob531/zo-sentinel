from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey


def get_api_key_creation_timeline(session: Session, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    cutoff = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=days - 1)
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    if session.get_bind().dialect.name == "sqlite":
        day_expression = func.strftime("%Y-%m-%d", ApiKey.created_at)
    else:
        day_expression = func.date_trunc("day", ApiKey.created_at)

    daily_stmt = (
        select(
            day_expression.label("date"),
            func.count(ApiKey.id).label("count"),
        )
        .where(ApiKey.created_at >= cutoff)
        .group_by(day_expression)
        .order_by(day_expression)
    )
    daily_rows = session.execute(daily_stmt).all()

    series = [
        {
            "date": _serialize_date(row.date),
            "count": int(row.count),
        }
        for row in daily_rows
    ]

    org_stmt = (
        select(
            ApiKey.org_id.label("org_id"),
            func.count(ApiKey.id).label("count"),
        )
        .where(ApiKey.created_at >= cutoff)
        .group_by(ApiKey.org_id)
        .order_by(func.count(ApiKey.id).desc(), ApiKey.org_id.asc())
        .limit(10)
    )
    org_rows = session.execute(org_stmt).all()

    by_org = [
        {
            "org_id": row.org_id,
            "count": int(row.count),
        }
        for row in org_rows
    ]

    return {
        "days": days,
        "total": sum(point["count"] for point in series),
        "series": series,
        "by_org": by_org,
    }


def _serialize_date(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


build_api_key_creation_timeline = get_api_key_creation_timeline
compute_api_key_creation_timeline = get_api_key_creation_timeline
get_api_key_timeline = get_api_key_creation_timeline
build_timeline = get_api_key_creation_timeline


def run_self_test() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(engine)

    with Session(engine) as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id="org-a",
                    key_hash="self-test-hash-1",
                    label="First",
                    created_at=now - timedelta(days=2),
                ),
                ApiKey(
                    id=2,
                    org_id="org-a",
                    key_hash="self-test-hash-2",
                    label="Second",
                    created_at=now - timedelta(days=1),
                ),
                ApiKey(
                    id=3,
                    org_id="org-b",
                    key_hash="self-test-hash-3",
                    label="Third",
                    created_at=now,
                ),
                ApiKey(
                    id=4,
                    org_id="org-b",
                    key_hash="self-test-hash-4",
                    label="Fourth",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/api-keys/timeline")
    def timeline(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_api_key_creation_timeline(session, days)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/api-keys/timeline")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["series"]) == 3
        assert payload["total"] == 4
        assert len(payload["by_org"]) == 2

    print("PASS")


if __name__ == "__main__":
    run_self_test()