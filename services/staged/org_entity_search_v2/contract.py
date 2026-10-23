from __future__ import annotations

import sys
from typing import List

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, Org, User
from .logic import OrgUser, get_org_users


def run() -> bool:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        org = Org(id="1", name="Test Org")
        session.add(org)
        session.flush()
        session.add_all(
            [
                User(
                    id="user-1",
                    org_id=org.id,
                    email="alice@example.test",
                    password_hash="test-hash-1",
                    role="member",
                    clerk_id="clerk-1",
                    clerk_synced_via="api",
                ),
                User(
                    id="user-2",
                    org_id=org.id,
                    email="bob@example.test",
                    password_hash="test-hash-2",
                    role="admin",
                    clerk_id="clerk-2",
                    clerk_synced_via="web",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/entities", response_model=List[OrgUser])
    def entities(org_id: int, session: Session = Depends(get_session)):
        return get_org_users(org_id=org_id, session=session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/entities", params={"org_id": 1})

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 2, body
    assert {row["email"] for row in body} == {
        "alice@example.test",
        "bob@example.test",
    }, body
    expected_fields = {
        "org_id",
        "name",
        "email",
        "role",
        "clerk_id",
        "clerk_synced_via",
    }
    assert all(set(row) == expected_fields for row in body), body
    assert all(row["org_id"] == 1 for row in body), body
    assert all(row["name"] == "Test Org User" for row in body), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)