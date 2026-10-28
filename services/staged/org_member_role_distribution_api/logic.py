from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


router = APIRouter(prefix="/api")


@router.get("/orgs/member-role-distribution")
def get_member_role_distribution(
    org_id: str | None = Query(default=None),
    role: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    org_query = select(Org.id, Org.name)
    user_query = select(User.org_id, User.role, func.count(User.id)).group_by(
        User.org_id, User.role
    )
    user_count_query = select(User.org_id, func.count(User.id)).group_by(User.org_id)
    api_key_count_query = select(ApiKey.org_id, func.count(ApiKey.id)).group_by(
        ApiKey.org_id
    )

    if org_id is not None:
        org_query = org_query.where(Org.id == org_id)
    if role is not None:
        user_query = user_query.where(User.role == role)

    org_rows = session.execute(org_query).all()
    org_ids = [row.id for row in org_rows]

    if not org_ids:
        return {
            "orgs": [],
            "total_orgs": 0,
            "total_users": 0,
        }

    user_query = user_query.where(User.org_id.in_(org_ids))
    user_count_query = user_count_query.where(User.org_id.in_(org_ids))
    api_key_count_query = api_key_count_query.where(ApiKey.org_id.in_(org_ids))

    role_counts: dict[Any, dict[str, int]] = {}
    for row in session.execute(user_query).all():
        role_counts.setdefault(row.org_id, {})[row.role or "unknown"] = int(row.count)

    user_counts = {
        row.org_id: int(row.count)
        for row in session.execute(user_count_query).all()
    }
    api_key_counts = {
        row.org_id: int(row.count)
        for row in session.execute(api_key_count_query).all()
    }

    result_orgs = []
    for row in org_rows:
        result_orgs.append(
            {
                "org_id": row.id,
                "org_name": row.name,
                "user_count": user_counts.get(row.id, 0),
                "api_key_count": api_key_counts.get(row.id, 0),
                "roles": role_counts.get(row.id, {}),
            }
        )

    return {
        "orgs": result_orgs,
        "total_orgs": len(result_orgs),
        "total_users": sum(item["user_count"] for item in result_orgs),
    }


def get_org_member_role_distribution(
    org_id: str | None = None,
    role: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_member_role_distribution(
        org_id=org_id,
        role=role,
        session=session,
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organization"),
                Org(id="org-2", name="Second Organization"),
            ]
        )
        session.commit()

        session.add_all(
            [
                User(
                    id="user-1",
                    email="admin@example.com",
                    org_id="org-1",
                    role="admin",
                    password_hash="secret-1",
                ),
                User(
                    id="user-2",
                    email="analyst@example.com",
                    org_id="org-1",
                    role="analyst",
                    password_hash="secret-2",
                ),
                User(
                    id="user-3",
                    email="viewer@example.com",
                    org_id="org-2",
                    role="viewer",
                    password_hash="secret-3",
                ),
            ]
        )
        session.add(
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash="hashed-key",
                label="Primary key",
            )
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/orgs/member-role-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_users"] == 3
    assert payload["total_orgs"] == 2
    assert payload["orgs"][0]["roles"] == {"admin": 1, "analyst": 1}
    assert payload["orgs"][1]["roles"] == {"viewer": 1}
    assert "password_hash" not in response.text
    assert "key_hash" not in response.text

    print("PASS")


if __name__ == "__main__":
    _run_self_test()