from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


router = APIRouter()


class ApiKeyInventoryRow(BaseModel):
    org_id: str
    org_name: str
    key_id: str
    label: Optional[str]
    created_at: datetime
    age_days: int
    member_count: int


class ApiKeyInventoryResponse(BaseModel):
    total: int
    generated_at: datetime
    rows: List[ApiKeyInventoryRow]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, generated_at: datetime) -> int:
    return max(0, int((generated_at - _as_utc(created_at)).total_seconds() // 86400))


@router.get(
    "/api/orgs/api-keys",
    response_model=ApiKeyInventoryResponse,
)
def list_org_api_keys(
    session: Session = Depends(get_session),
    org_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> ApiKeyInventoryResponse:
    statement = (
        select(
            ApiKey.id.label("key_id"),
            ApiKey.org_id.label("org_id"),
            Org.name.label("org_name"),
            ApiKey.label.label("label"),
            ApiKey.created_at.label("created_at"),
            func.count(User.id).label("member_count"),
        )
        .join(Org, Org.id == ApiKey.org_id)
        .outerjoin(User, User.org_id == ApiKey.org_id)
        .group_by(
            ApiKey.id,
            ApiKey.org_id,
            Org.name,
            ApiKey.label,
            ApiKey.created_at,
        )
        .order_by(ApiKey.created_at.desc(), ApiKey.id)
        .limit(limit)
    )

    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    generated_at = datetime.now(timezone.utc)
    rows = []
    for result in session.execute(statement):
        created_at = _as_utc(result.created_at)
        rows.append(
            ApiKeyInventoryRow(
                org_id=result.org_id,
                org_name=result.org_name,
                key_id=result.key_id,
                label=result.label,
                created_at=created_at,
                age_days=_age_days(created_at, generated_at),
                member_count=int(result.member_count or 0),
            )
        )

    return ApiKeyInventoryResponse(
        total=len(rows),
        generated_at=generated_at,
        rows=rows,
    )


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
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    label VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO orgs (id, name) VALUES (:id, :name)"),
            [{"id": "org-1", "name": "Example Org One"},
             {"id": "org-2", "name": "Example Org Two"}],
        )
        connection.execute(
            text("INSERT INTO users (id, org_id) VALUES (:id, :org_id)"),
            [{"id": "user-1", "org_id": "org-1"},
             {"id": "user-2", "org_id": "org-2"}],
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys
                    (id, org_id, key_hash, label, created_at)
                VALUES
                    (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [
                {
                    "id": "key-1",
                    "org_id": "org-1",
                    "key_hash": "secret-material-1",
                    "label": "primary",
                    "created_at": (
                        datetime.now(timezone.utc) - timezone.utc.utcoffset(None)
                    ).isoformat(),
                },
                {
                    "id": "key-2",
                    "org_id": "org-2",
                    "key_hash": "secret-material-2",
                    "label": "secondary",
                    "created_at": (
                        datetime.now(timezone.utc) - timezone.utc.utcoffset(None)
                    ).isoformat(),
                },
            ],
        )

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/api-keys")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert all("key_hash" not in row for row in payload["rows"])
    assert any(row["member_count"] == 1 for row in payload["rows"])
    print("PASS")