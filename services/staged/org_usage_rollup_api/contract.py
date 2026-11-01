from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, func, select, text
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


def _timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days_active(created_at: object) -> int:
    started = _as_datetime(created_at)
    if started is None:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - started).days)


def _parse_org_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def create_test_app() -> FastAPI:
    test_app = FastAPI()

    @test_app.get("/api/orgs/usage")
    def org_usage_rollup(
        include_orgs: Optional[str] = Query(default=None),
        min_api_keys: int = Query(default=0, ge=0),
        session: OrmSession = Depends(get_session),
    ) -> dict:
        org_ids = _parse_org_ids(include_orgs)

        statement = (
            select(
                Org.id.label("org_id"),
                Org.name.label("org_name"),
                Org.created_at.label("created_at"),
                func.count(func.distinct(User.id)).label("user_count"),
                func.count(
                    func.distinct(
                        case((User.role == "admin", User.id), else_=None)
                    )
                ).label("admin_count"),
                func.count(func.distinct(ApiKey.id)).label("api_key_count"),
                func.min(ApiKey.created_at).label("oldest_key_created_at"),
                func.max(ApiKey.created_at).label("newest_key_created_at"),
            )
            .select_from(Org)
            .outerjoin(User, User.org_id == Org.id)
            .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        )

        if org_ids:
            statement = statement.where(Org.id.in_(org_ids))

        statement = (
            statement.group_by(Org.id, Org.name, Org.created_at)
            .having(func.count(func.distinct(ApiKey.id)) >= min_api_keys)
            .order_by(Org.name, Org.id)
        )

        orgs = []
        for row in session.execute(statement).mappings():
            created_at = row["created_at"]
            orgs.append(
                {
                    "org_id": row["org_id"],
                    "org_name": row["org_name"],
                    "created_at": _timestamp(created_at),
                    "user_count": int(row["user_count"]),
                    "admin_count": int(row["admin_count"]),
                    "api_key_count": int(row["api_key_count"]),
                    "oldest_key_created_at": _timestamp(
                        row["oldest_key_created_at"]
                    ),
                    "newest_key_created_at": _timestamp(
                        row["newest_key_created_at"]
                    ),
                    "days_active": _days_active(created_at),
                }
            )

        return {
            "orgs": orgs,
            "totals": {
                "org_count": len(orgs),
                "user_count": sum(org["user_count"] for org in orgs),
                "api_key_count": sum(org["api_key_count"] for org in orgs),
            },
        }

    return test_app


def _create_schema(engine) -> None:
    statements = (
        """
        CREATE TABLE orgs (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE users (
            id VARCHAR PRIMARY KEY,
            org_id VARCHAR NOT NULL,
            role VARCHAR NOT NULL DEFAULT 'member',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE api_keys (
            id VARCHAR PRIMARY KEY,
            org_id VARCHAR NOT NULL,
            key_hash VARCHAR NOT NULL,
            label VARCHAR NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _seed(session: OrmSession) -> None:
    session.execute(
        text(
            "INSERT INTO orgs (id, name, created_at) VALUES "
            "(:id, :name, :created_at)"
        ),
        [
            {
                "id": "org-1",
                "name": "Acme Org",
                "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            {
                "id": "org-2",
                "name": "Beta Org",
                "created_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
            },
        ],
    )
    session.execute(
        text(
            "INSERT INTO users (id, org_id, role, created_at) VALUES "
            "(:id, :org_id, :role, :created_at)"
        ),
        [
            {
                "id": "user-1",
                "org_id": "org-1",
                "role": "admin",
                "created_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
            {
                "id": "user-2",
                "org_id": "org-1",
                "role": "member",
                "created_at": datetime(2024, 1, 3, tzinfo=timezone.utc),
            },
            {
                "id": "user-3",
                "org_id": "org-2",
                "role": "admin",
                "created_at": datetime(2024, 2, 2, tzinfo=timezone.utc),
            },
        ],
    )
    session.execute(
        text(
            "INSERT INTO api_keys "
            "(id, org_id, key_hash, label, created_at) VALUES "
            "(:id, :org_id, :key_hash, :label, :created_at)"
        ),
        [
            {
                "id": "key-1",
                "org_id": "org-1",
                "key_hash": "hash-1",
                "label": "Primary",
                "created_at": datetime(2024, 1, 4, tzinfo=timezone.utc),
            },
            {
                "id": "key-2",
                "org_id": "org-2",
                "key_hash": "hash-2",
                "label": "Primary",
                "created_at": datetime(2024, 2, 3, tzinfo=timezone.utc),
            },
        ],
    )
    session.commit()


def run_contract() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    _create_schema(engine)
    with session_factory() as session:
        _seed(session)

    test_app = create_test_app()

    def override_get_session() -> Iterator[OrmSession]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/orgs/usage")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert len(payload["orgs"]) == 2, payload

            org_with_two_users = next(
                org for org in payload["orgs"] if org["org_id"] == "org-1"
            )
            assert org_with_two_users["user_count"] == 2, payload
            assert payload["totals"]["org_count"] == 2, payload
    finally:
        engine.dispose()

    print("PASS")
    return True


def main() -> None:
    run_contract()


if __name__ == "__main__":
    main()