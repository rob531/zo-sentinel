from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


class ApiKeyInventoryRow(BaseModel):
    org_id: str
    org_name: str
    key_id: str
    label: str
    created_at: datetime
    age_days: int
    member_count: int


class ApiKeyInventoryResponse(BaseModel):
    total: int
    generated_at: datetime
    rows: List[ApiKeyInventoryRow]


router = APIRouter()


@router.get("/api/orgs/api-keys", response_model=ApiKeyInventoryResponse)
def list_org_api_keys(
    org_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ApiKeyInventoryResponse:
    filters = []
    if org_id is not None:
        filters.append(ApiKey.org_id == org_id)

    member_count = (
        select(func.count(User.id))
        .where(User.org_id == ApiKey.org_id)
        .correlate(ApiKey)
        .scalar_subquery()
    )

    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            Org.name,
            ApiKey.label,
            ApiKey.created_at,
            member_count.label("member_count"),
        )
        .join(Org, Org.id == ApiKey.org_id)
        .where(*filters)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.asc())
        .limit(limit)
    )

    count_statement = (
        select(func.count(ApiKey.id))
        .join(Org, Org.id == ApiKey.org_id)
        .where(*filters)
    )

    total = int(session.execute(count_statement).scalar_one())
    generated_at = datetime.now(timezone.utc)
    rows: List[ApiKeyInventoryRow] = []

    for key_id, row_org_id, org_name, label, created_at, row_member_count in session.execute(statement):
        if created_at.tzinfo is None:
            normalized_created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            normalized_created_at = created_at.astimezone(timezone.utc)

        age_days = max(
            0,
            int((generated_at - normalized_created_at).total_seconds() // 86400),
        )

        rows.append(
            ApiKeyInventoryRow(
                org_id=str(row_org_id),
                org_name=org_name,
                key_id=str(key_id),
                label=label,
                created_at=normalized_created_at,
                age_days=age_days,
                member_count=int(row_member_count or 0),
            )
        )

    return ApiKeyInventoryResponse(
        total=total,
        generated_at=generated_at,
        rows=rows,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with test_session_factory() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organisation"),
                Org(id="org-2", name="Second Organisation"),
            ]
        )
        session.flush()
        session.add_all(
            [
                User(
                    id="user-1",
                    org_id="org-1",
                    email="admin@example.test",
                    password_hash="not-a-real-password-hash",
                    role="admin",
                ),
                User(
                    id="user-2",
                    org_id="org-2",
                    email="member@example.test",
                    password_hash="not-a-real-password-hash",
                    role="member",
                ),
            ]
        )
        session.add_all(
            [
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash="secret-hash-1",
                    label="Primary key",
                    created_at=now - timedelta(days=30),
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-2",
                    key_hash="secret-hash-2",
                    label="Secondary key",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/api-keys")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2, payload
    assert len(payload["rows"]) == 2, payload
    assert all("key_hash" not in row for row in payload["rows"]), payload
    assert all("secret-hash" not in response.text for _ in [0]), response.text
    assert any(
        row["org_id"] == "org-1" and row["member_count"] == 1
        for row in payload["rows"]
    ), payload

    engine.dispose()
    print("PASS")