"""Acceptance self-test for the org_user_access_roster service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, Org, User

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    try:
        session.add_all(
            [
                Org(id="org-1", name="Organization One"),
                Org(id="org-2", name="Organization Two"),
                User(
                    id="user-1",
                    email="admin@example.com",
                    org_id="org-1",
                    password_hash="test-password-hash",
                    role="admin",
                ),
                User(
                    id="user-2",
                    email="analyst@example.com",
                    org_id="org-1",
                    password_hash="test-password-hash",
                    role="analyst",
                ),
                User(
                    id="user-3",
                    email="viewer@example.com",
                    org_id="org-1",
                    password_hash="test-password-hash",
                    role="viewer",
                ),
                User(
                    id="user-4",
                    email="second-admin@example.com",
                    org_id="org-2",
                    password_hash="test-password-hash",
                    role="admin",
                ),
                User(
                    id="user-5",
                    email="second-viewer@example.com",
                    org_id="org-2",
                    password_hash="test-password-hash",
                    role="viewer",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/orgs/users")
    assert response.status_code == 200, response.text

    body = response.json()
    assert isinstance(body.get("orgs"), list), body
    assert len(body["orgs"]) == 2, body
    assert sum(org.get("user_count", 0) for org in body["orgs"]) == 5, body
    assert any(
        org.get("roles", {}).get("admin", 0) >= 1
        for org in body["orgs"]
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)