from fastapi import Depends

from app.db import get_session
from app.models import ApiKey, Org, User


def compute_access_matrix(
    orgs: list[dict],
    users: list[dict],
    api_keys: list[dict],
) -> dict:
    org_ids = {str(org.get("id")) for org in orgs}
    role_counts_by_org: dict[str, dict[str, int]] = {
        str(org.get("id")): {} for org in orgs
    }
    user_counts = {str(org.get("id")): 0 for org in orgs}
    admin_counts = {str(org.get("id")): 0 for org in orgs}
    api_key_counts = {str(org.get("id")): 0 for org in orgs}
    orphan_user_count = 0

    for user in users:
        org_id = str(user.get("org_id"))
        if org_id not in org_ids:
            orphan_user_count += 1
            continue

        user_counts[org_id] += 1
        role = str(user.get("role"))
        role_counts_by_org[org_id][role] = (
            role_counts_by_org[org_id].get(role, 0) + 1
        )
        if role == "admin":
            admin_counts[org_id] += 1

    for api_key in api_keys:
        org_id = str(api_key.get("org_id"))
        if org_id in api_key_counts:
            api_key_counts[org_id] += 1

    matrix_orgs = []
    for org in orgs:
        org_id = str(org.get("id"))
        admin_count = admin_counts[org_id]
        matrix_orgs.append(
            {
                "org_id": org_id,
                "name": str(org.get("name", "")),
                "user_count": user_counts[org_id],
                "role_counts": role_counts_by_org[org_id],
                "admin_count": admin_count,
                "api_key_count": api_key_counts[org_id],
                "has_admin": admin_count > 0,
            }
        )

    return {
        "orgs": matrix_orgs,
        "orphan_user_count": orphan_user_count,
        "orgs_without_admin": sum(
            1 for org in matrix_orgs if not org["has_admin"]
        ),
        "total_users": len(users),
    }


def load_access_matrix(session=Depends(get_session)) -> dict:
    org_rows = session.query(
        Org.id,
        Org.name,
        Org.created_at,
    ).all()
    user_rows = session.query(
        User.id,
        User.email,
        User.org_id,
        User.role,
        User.created_at,
    ).all()
    api_key_rows = session.query(
        ApiKey.id,
        ApiKey.org_id,
        ApiKey.label,
        ApiKey.created_at,
    ).all()

    orgs = [
        {
            "id": row.id,
            "name": row.name,
            "created_at": row.created_at,
        }
        for row in org_rows
    ]
    users = [
        {
            "id": row.id,
            "email": row.email,
            "org_id": row.org_id,
            "role": row.role,
            "created_at": row.created_at,
        }
        for row in user_rows
    ]
    api_keys = [
        {
            "id": row.id,
            "org_id": row.org_id,
            "label": row.label,
            "created_at": row.created_at,
        }
        for row in api_key_rows
    ]

    return compute_access_matrix(orgs, users, api_keys)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine, checkfirst=True)
    User.__table__.create(engine, checkfirst=True)
    ApiKey.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organisation"),
                Org(id="org-2", name="Second Organisation"),
                User(
                    id="user-1",
                    email="admin@example.com",
                    org_id="org-1",
                    role="admin",
                    password_hash="test-password-hash-1",
                ),
                User(
                    id="user-2",
                    email="member@example.com",
                    org_id="org-1",
                    role="member",
                    password_hash="test-password-hash-2",
                ),
                User(
                    id="user-3",
                    email="orphan@example.com",
                    org_id="missing-org",
                    role="member",
                    password_hash="test-password-hash-3",
                ),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    label="First key",
                    key_hash="test-key-hash-1",
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-2",
                    label="Second key",
                    key_hash="test-key-hash-2",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with Session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/orgs/user-access-matrix")
    def user_access_matrix(matrix=Depends(load_access_matrix)):
        return {
            **matrix,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    response = TestClient(that_app).get("/api/orgs/user-access-matrix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_users"] == 3
    assert payload["orphan_user_count"] == 1
    assert payload["orgs"][0]["user_count"] == 2
    assert payload["orgs"][0]["has_admin"] is True
    assert payload["orgs"][0]["admin_count"] == 1
    assert payload["orgs"][0]["api_key_count"] == 1
    assert payload["orgs"][1]["api_key_count"] == 1
    assert payload["orgs_without_admin"] == 1
    assert "generated_at" in payload

    engine.dispose()
    print("PASS")