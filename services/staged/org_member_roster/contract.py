"""Acceptance self-test for the org_member_roster service."""

from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import ApiKey, Base, Org, User

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
                    email="analyst@example.test",
                    org_id="org-1",
                    password_hash="password-hash-1",
                    role="analyst",
                ),
                User(
                    id="user-2",
                    email="viewer@example.test",
                    org_id="org-1",
                    password_hash="password-hash-2",
                    role="viewer",
                ),
                User(
                    id="user-3",
                    email="admin@example.test",
                    org_id="org-2",
                    password_hash="password-hash-3",
                    role="admin",
                ),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash="raw-key-hash-org-1",
                    label="org-1-key",
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-2",
                    key_hash="raw-key-hash-org-2",
                    label="org-2-key",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/orgs/org-1/members")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["org_id"] == "org-1", body
    assert body["member_count"] == 2, body
    assert body["by_role"] == {"analyst": 1, "viewer": 1}, body
    assert body["api_key_count"] == 1, body
    assert len(body["members"]) == 2, body
    assert {member["role"] for member in body["members"]} == {"analyst", "viewer"}, body

    response_text = response.text.lower()
    assert "key_hash" not in response_text, response.text
    assert "raw-key-hash-org-1" not in response.text, response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)