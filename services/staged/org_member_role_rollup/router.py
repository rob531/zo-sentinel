from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Org, User

from .logic import get_org_member_role_rollup

router = APIRouter(prefix="/api", tags=["org-member-role-rollup"])


class MemberRoleCounts(BaseModel):
    admin: int = 0
    analyst: int = 0
    viewer: int = 0
    other: int = 0


class OrgMemberRoleRollup(BaseModel):
    org_id: int
    org_name: str
    created_at: datetime
    member_count: int
    roles: MemberRoleCounts
    clerk_synced: int
    clerk_missing: int
    newest_member_created_at: Optional[datetime] = None


class OrgMemberRoleRollupResponse(BaseModel):
    generated_at: datetime
    total_orgs: int
    total_users: int
    orgs: list[OrgMemberRoleRollup]


@router.get(
    "/orgs/member-roles",
    response_model=OrgMemberRoleRollupResponse,
)
def get_member_roles(
    org_id: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> OrgMemberRoleRollupResponse:
    return get_org_member_role_rollup(session, org_id)


def _run_self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Org.__table__.create(engine)
    User.__table__.create(engine)

    with TestingSession() as session:
        session.add_all(
            [
                Org(id=1, name="Alpha", created_at=datetime(2025, 1, 1)),
                Org(id=2, name="Beta", created_at=datetime(2025, 1, 2)),
                Org(id=3, name="Gamma", created_at=datetime(2025, 1, 3)),
            ]
        )
        session.add_all(
            [
                User(
                    id=1,
                    email="admin@example.com",
                    org_id=1,
                    role="admin",
                    password_hash="test",
                    clerk_id="clerk_admin",
                    clerk_synced_via="manual",
                    clerk_created_at=datetime(2025, 1, 4),
                    created_at=datetime(2025, 1, 4),
                ),
                User(
                    id=2,
                    email="analyst@example.com",
                    org_id=1,
                    role="analyst",
                    password_hash="test",
                    clerk_id="clerk_analyst",
                    clerk_synced_via="manual",
                    clerk_created_at=datetime(2025, 1, 5),
                    created_at=datetime(2025, 1, 5),
                ),
                User(
                    id=3,
                    email="viewer@example.com",
                    org_id=1,
                    role="viewer",
                    password_hash="test",
                    clerk_id="clerk_viewer",
                    clerk_synced_via="manual",
                    clerk_created_at=datetime(2025, 1, 6),
                    created_at=datetime(2025, 1, 6),
                ),
                User(
                    id=4,
                    email="other@example.com",
                    org_id=2,
                    role="owner",
                    password_hash="test",
                    clerk_id="clerk_other",
                    clerk_synced_via="manual",
                    clerk_created_at=datetime(2025, 1, 7),
                    created_at=datetime(2025, 1, 7),
                ),
                User(
                    id=5,
                    email="beta-admin@example.com",
                    org_id=2,
                    role="admin",
                    password_hash="test",
                    clerk_id="clerk_beta_admin",
                    clerk_synced_via="manual",
                    clerk_created_at=datetime(2025, 1, 8),
                    created_at=datetime(2025, 1, 8),
                ),
                User(
                    id=6,
                    email="gamma-viewer@example.com",
                    org_id=3,
                    role="viewer",
                    password_hash="test",
                    clerk_id=None,
                    clerk_synced_via=None,
                    clerk_created_at=None,
                    created_at=datetime(2025, 1, 9),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/member-roles")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_orgs"] == 3
        assert payload["total_users"] == 6
        assert sum(org["member_count"] for org in payload["orgs"]) == 6

        beta = next(org for org in payload["orgs"] if org["org_id"] == 2)
        assert beta["roles"]["other"] == 1

        gamma = next(org for org in payload["orgs"] if org["org_id"] == 3)
        assert gamma["clerk_missing"] == 1

        filtered = client.get("/api/orgs/member-roles", params={"org_id": 3})
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total_orgs"] == 1
        assert filtered_payload["total_users"] == 1
        assert filtered_payload["orgs"][0]["org_id"] == 3

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()