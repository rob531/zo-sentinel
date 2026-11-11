from collections import defaultdict

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Org, User

KNOWN_ROLES = ("admin", "analyst", "viewer")
UNKNOWN_ROLE = "unknown"

router = APIRouter(prefix="/api")


@router.get("/orgs/users/roles")
def org_user_role_matrix(session: Session = Depends(get_session)) -> dict:
    statement = (
        select(
            Org.id,
            Org.name,
            User.role,
            func.count(User.id),
        )
        .select_from(Org)
        .outerjoin(User, User.org_id == Org.id)
        .group_by(Org.id, User.role)
    )

    role_counts = {role: 0 for role in (*KNOWN_ROLES, UNKNOWN_ROLE)}
    orgs = {}

    for org_id, org_name, role, user_count in session.execute(statement):
        org_key = str(org_id)
        if org_key not in orgs:
            orgs[org_key] = {
                "org_id": org_key,
                "org_name": str(org_name),
                "total_users": 0,
                "roles": {name: 0 for name in (*KNOWN_ROLES, UNKNOWN_ROLE)},
            }

        count = int(user_count or 0)
        if count == 0:
            continue

        bucket = role if role in KNOWN_ROLES else UNKNOWN_ROLE
        orgs[org_key]["total_users"] += count
        orgs[org_key]["roles"][bucket] += count
        role_counts[bucket] += count

    org_list = list(orgs.values())
    return {
        "orgs": org_list,
        "totals": {
            "orgs": len(org_list),
            "users": sum(org["total_users"] for org in org_list),
            "roles": role_counts,
        },
    }


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    role VARCHAR,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    (1, 'Acme Corp', CURRENT_TIMESTAMP),
                    (2, 'Globex Corp', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, org_id, role, created_at)
                VALUES
                    (1, 1, 'admin', CURRENT_TIMESTAMP),
                    (2, 1, 'analyst', CURRENT_TIMESTAMP),
                    (3, 1, 'viewer', CURRENT_TIMESTAMP),
                    (4, 2, 'analyst', CURRENT_TIMESTAMP),
                    (5, 2, 'viewer', CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        session = Session(bind=engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/users/roles")
        assert response.status_code == 200, response.text
        payload = response.json()

    org_ids = {org["org_id"] for org in payload["orgs"]}
    assert org_ids == {"1", "2"}, payload
    assert payload["totals"]["orgs"] == 2, payload
    assert payload["totals"]["users"] == 5, payload
    assert payload["totals"]["roles"]["admin"] == 1, payload

    admin_count = sum(org["roles"]["admin"] for org in payload["orgs"])
    assert admin_count == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()