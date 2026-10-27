from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey


def _daily_created_at_expression(db: Session):
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        return func.date_trunc("day", ApiKey.created_at)
    return func.date(ApiKey.created_at)


def _as_iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)[:10]


def get_creation_cohort(
    days: int,
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    if days < 1:
        raise ValueError("days must be at least 1")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    day_expression = _daily_created_at_expression(db)

    statement = (
        db.query(
            day_expression.label("date"),
            func.count(ApiKey.id).label("created"),
            func.count(func.distinct(ApiKey.org_id)).label("orgs"),
        )
        .filter(ApiKey.created_at >= cutoff, ApiKey.created_at <= now)
        .group_by(day_expression)
        .order_by(day_expression)
    )

    return [
        {
            "date": _as_iso_date(row.date),
            "created": int(row.created),
            "orgs": int(row.orgs),
        }
        for row in statement.all()
    ]


def get_creation_cohort_response(
    days: int,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "days": days,
        "series": get_creation_cohort(days, db),
    }


get_creation_cohort_data = get_creation_cohort_response
get_api_key_creation_cohort = get_creation_cohort
get_creation_cohort_series = get_creation_cohort
get_api_key_creation_series = get_creation_cohort


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id TEXT,
                    label TEXT,
                    key_hash TEXT,
                    created_at TIMESTAMP
                )
                """
            )
        )
        seed_time = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, label, key_hash, created_at)
                VALUES
                    (1, 'org-a', 'first', 'hash-1', :first_at),
                    (2, 'org-a', 'second', 'hash-2', :first_at),
                    (3, 'org-b', 'third', 'hash-3', :second_at)
                """
            ),
            {
                "first_at": seed_time - timedelta(minutes=5),
                "second_at": seed_time - timedelta(days=1),
            },
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/api-keys/creation-cohort")
    def creation_cohort_endpoint(
        days: int = 7,
        db: Session = Depends(get_session),
    ):
        return {
            "days": days,
            "series": get_creation_cohort(days, db),
        }

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/api-keys/creation-cohort", params={"days": 7})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 7
        assert len(payload["series"]) == 2, payload
        assert sum(point["created"] for point in payload["series"]) == 3, payload

    print("PASS")