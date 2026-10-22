from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User

from .logic import get_role_distribution


router = APIRouter(prefix="/api", tags=["org-user-role-distribution"])


class RoleDistributionResponse(BaseModel):
    org_id: int
    total_users: int
    by_role: dict[str, int]
    admins: int
    recent_signups_30d: int


@router.get(
    "/orgs/role-distribution",
    response_model=RoleDistributionResponse,
    summary="Get user role distribution for an organization",
)
def org_user_role_distribution(
    org_id: int,
    session: Session = Depends(get_session),
) -> RoleDistributionResponse:
    return get_role_distribution(org_id, session)


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Org.__table__.create(engine)
    User.__table__.create(engine)

    now = datetime.now(timezone.utc)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                Org(id=1, name="Primary Organization", created_at=now),
                Org(id=2, name="Other Organization", created_at=now),
                User(
                    id=1,
                    email="admin@example.com",
                    org_id=1,
                    role="admin",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id=2,
                    email="member-1@example.com",
                    org_id=1,
                    role="member",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id=3,
                    email="member-2@example.com",
                    org_id=1,
                    role="member",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id=4,
                    email="member-3@example.com",
                    org_id=1,
                    role="member",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id=5,
                    email="other-org@example.com",
                    org_id=2,
                    role="admin",
                    created_at=now,
                    password_hash="test",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/role-distribution", params={"org_id": 1})
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["org_id"] == 1
        assert payload["total_users"] == 4
        assert payload["by_role"] == {"admin": 1, "member": 3}
        assert payload["admins"] == 1
        assert payload["recent_signups_30d"] == 4
        assert "other-org@example.com" not in str(payload)

    print("PASS")