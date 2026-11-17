from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func, literal_column, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey


class CreationRecencyResponse(BaseModel):
    days: int
    series: list[dict[str, Any]]
    total: int


def _normalize_day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_creation_recency_series(session: Session, days: int = 30) -> list[dict[str, Any]]:
    days = int(days)
    if days < 1:
        raise ValueError("days must be at least 1")

    now = _utc_now()
    start_date = now.date() - timedelta(days=days - 1)
    cutoff = datetime.combine(start_date, time.min, tzinfo=timezone.utc)

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        day_expression = literal_column("date_trunc('day', api_keys.created_at)")
        statement = (
            select(day_expression.label("day"), func.count(ApiKey.id).label("count"))
            .select_from(ApiKey)
            .where(ApiKey.created_at >= cutoff, ApiKey.created_at <= now)
            .group_by(day_expression)
            .order_by(day_expression)
        )
    else:
        day_expression = func.date(ApiKey.created_at)
        statement = (
            select(day_expression.label("day"), func.count(ApiKey.id).label("count"))
            .select_from(ApiKey)
            .where(ApiKey.created_at >= cutoff, ApiKey.created_at <= now)
            .group_by(day_expression)
            .order_by(day_expression)
        )

    rows = session.execute(statement).all()
    return [
        {"date": _normalize_day(row.day), "count": int(row.count)}
        for row in rows
    ]


def get_creation_recency(session: Session, days: int = 30) -> dict[str, Any]:
    normalized_days = int(days)
    series = get_creation_recency_series(session, normalized_days)
    return {
        "days": normalized_days,
        "series": series,
        "total": sum(item["count"] for item in series),
    }


def compute_api_key_creation_recency(session: Session, days: int = 30) -> dict[str, Any]:
    return get_creation_recency(session, days)


def get_creation_recency_data(session: Session, days: int = 30) -> dict[str, Any]:
    return get_creation_recency(session, days)


def get_api_key_creation_recency(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if isinstance(days, Session):
        days, session = session, days
    return get_creation_recency(session, days)


def get_api_key_creation_recency_data(
    session: Session,
    days: int = 30,
) -> dict[str, Any]:
    return get_creation_recency(session, days)


def create_app() -> FastAPI:
    application = FastAPI()

    @application.get(
        "/api/api-keys/creation-recency",
        response_model=CreationRecencyResponse,
    )
    def creation_recency(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_creation_recency(db, days)

    return application


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    key_hash TEXT NOT NULL,
                    label TEXT,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    seed_rows = [
        (1, 1, "hash-1", "one", today),
        (2, 1, "hash-2", "two", today),
        (3, 1, "hash-3", "three", today),
        (4, 2, "hash-4", "four", yesterday),
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [
                {
                    "id": row[0],
                    "org_id": row[1],
                    "key_hash": row[2],
                    "label": row[3],
                    "created_at": row[4],
                }
                for row in seed_rows
            ],
        )

    TestingSessionLocal = sessionmaker = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker
    test_session_factory = sessionmaker(bind=engine)

    def override_get_session():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/api-keys/creation-recency", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert len(payload["series"]) == 2
        counts = {item["date"]: item["count"] for item in payload["series"]}
        assert counts[today.date().isoformat()] == 3
        assert counts[yesterday.date().isoformat()] == 1
        assert payload["total"] == 4

    print("PASS")