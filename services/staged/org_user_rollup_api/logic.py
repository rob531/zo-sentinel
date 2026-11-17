from __future__ import annotations

from datetime import datetime

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


def get_org_rollup(session: Session = Depends(get_session)) -> list[dict]:
    query = (
        select(
            Org.id.label("org_id"),
            Org.name.label("name"),
            Org.created_at.label("created_at"),
            func.count(func.distinct(User.id)).label("user_count"),
            func.count(func.distinct(ApiKey.id)).label("api_key_count"),
        )
        .outerjoin(User, User.org_id == Org.id)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .group_by(Org.id, Org.name, Org.created_at)
        .order_by(Org.created_at.asc(), Org.id.asc())
    )

    return [
        {
            "org_id": org_id,
            "name": name,
            "created_at": created_at,
            "user_count": user_count,
            "api_key_count": api_key_count,
        }
        for org_id, name, created_at, user_count, api_key_count in session.execute(query)
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    Org.__table__.create(test_engine)
    User.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    with TestSession() as session:
        session.add_all(
            [
                Org(
                    id="org-1",
                    name="First Organization",
                    created_at=datetime(2024, 1, 1, 0, 0, 0),
                ),
                Org(
                    id="org-2",
                    name="Second Organization",
                    created_at=datetime(2024, 1, 2, 0, 0, 0),
                ),
                User(
                    id="user-1",
                    clerk_id="clerk-1",
                    email="one@example.com",
                    password_hash="hash-1",
                    role="member",
                    org_id="org-1",
                    created_at=datetime(2024, 1, 3, 0, 0, 0),
                ),
                User(
                    id="user-2",
                    clerk_id="clerk-2",
                    email="two@example.com",
                    password_hash="hash-2",
                    role="member",
                    org_id="org-1",
                    created_at=datetime(2024, 1, 4, 0, 0, 0),
                ),
                User(
                    id="user-3",
                    clerk_id="clerk-3",
                    email="three@example.com",
                    password_hash="hash-3",
                    role="member",
                    org_id="org-2",
                    created_at=datetime(2024, 1, 5, 0, 0, 0),
                ),
                ApiKey(
                    id="key-1",
                    key_hash="key-hash-1",
                    label="First key",
                    org_id="org-1",
                    created_at=datetime(2024, 1, 6, 0, 0, 0),
                ),
                ApiKey(
                    id="key-2",
                    key_hash="key-hash-2",
                    label="Second key",
                    org_id="org-2",
                    created_at=datetime(2024, 1, 7, 0, 0, 0),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/org/rollup")
    def org_rollup_endpoint(orgs: list[dict] = Depends(get_org_rollup)):
        return {"orgs": orgs}

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/org/rollup")
    assert response.status_code == 200
    assert len(response.json()["orgs"]) == 2
    assert response.json()["orgs"][0]["user_count"] == 2
    print("PASS")