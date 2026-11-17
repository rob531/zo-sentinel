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
    session.add(Org(id="org_1", name="Example Organization"))
    session.add(
        User(
            id="user_1",
            email="alice@example.com",
            org_id="org_1",
            role="admin",
            password_hash="password-hash-alice",
            clerk_id="clerk_alice",
        )
    )
    session.add(
        User(
            id="user_2",
            email="bob@example.com",
            org_id="org_1",
            role="member",
            password_hash="password-hash-bob",
        )
    )
    session.add(
        ApiKey(
            id="key_1",
            org_id="org_1",
            key_hash="hash-one-123456789",
            label="primary",
        )
    )
    session.add(
        ApiKey(
            id="key_2",
            org_id="org_1",
            key_hash="hash-two-987654321",
            label="secondary",
        )
    )
    session.commit()
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

    response = client.get("/api/orgs/org_1/roster")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["org"]["id"] == "org_1", body
    assert body["org"]["name"] == "Example Organization", body
    assert "created_at" in body["org"], body

    users = body["users"]
    assert len(users) == 2, body
    assert {user["email"] for user in users} == {
        "alice@example.com",
        "bob@example.com",
    }, body
    assert all("has_clerk_identity" in user for user in users), body

    api_keys = body["api_keys"]
    assert len(api_keys) == 2, body
    for entry in api_keys:
        assert "key_hash" not in entry, body
        prefix = entry["key_hash_prefix"]
        assert isinstance(prefix, str), body
        assert len(prefix) <= 8, body
        assert prefix != "hash-one-123456789", body
        assert prefix != "hash-two-987654321", body

    missing = client.get("/api/orgs/nope/roster")
    assert missing.status_code == 404, missing.text
    assert missing.json() == {"error": "org_not_found"}, missing.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)