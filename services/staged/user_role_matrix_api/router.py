from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User
from .logic import get_user_role_matrix

router = APIRouter(prefix="/api")


@router.get("/orgs/user-role-matrix")
def user_role_matrix(session: Session = Depends(get_session)):
    return get_user_role_matrix(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    assert Org.__tablename__ == "orgs"
    assert User.__tablename__ == "users"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    created_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT,
                    org_id TEXT,
                    role TEXT,
                    created_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    ('org-a', 'Alpha', '2025-01-01T00:00:00'),
                    ('org-b', 'Beta', '2025-01-01T00:00:00'),
                    ('org-c', 'Gamma', '2025-01-01T00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role, created_at)
                VALUES
                    ('user-1', 'one@example.com', 'org-a', 'admin', '2025-01-01T00:00:00'),
                    ('user-2', 'two@example.com', 'org-a', 'analyst', '2025-01-01T00:00:00'),
                    ('user-3', 'three@example.com', 'org-b', 'admin', '2025-01-01T00:00:00'),
                    ('user-4', 'four@example.com', 'org-c', 'analyst', '2025-01-01T00:00:00'),
                    ('user-5', 'five@example.com', 'org-c', NULL, '2025-01-01T00:00:00')
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: SessionLocal()

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/user-role-matrix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_users"] == 5, payload
        assert payload["total_orgs"] == 3, payload
        org_a = next(org for org in payload["orgs"] if org["org_id"] == "org-a")
        assert org_a["roles"] == {"admin": 1, "analyst": 1}, payload
        assert sum(len(org["roles"].values()) for org in payload["orgs"]) == 5, payload

    print("PASS")