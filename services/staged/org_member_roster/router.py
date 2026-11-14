"""HTTP surface for organization membership and API-key usage."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_members, get_org_usage

router = APIRouter(prefix="/api", tags=["org_member_roster"])


class OrgMember(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime


class OrgMembersResponse(BaseModel):
    org_id: str
    member_count: int
    by_role: dict[str, int]
    members: list[OrgMember]
    api_key_count: int


class OrgUsageResponse(BaseModel):
    org_id: str
    api_key_count: int


@router.get("/orgs/{org_id}/members", response_model=OrgMembersResponse)
def list_org_members(
    org_id: str, db: Session = Depends(get_session)
) -> OrgMembersResponse:
    return OrgMembersResponse(**get_org_members(db, org_id))


@router.get("/orgs/{org_id}/usage", response_model=OrgUsageResponse)
def get_usage(
    org_id: str, db: Session = Depends(get_session)
) -> OrgUsageResponse:
    return OrgUsageResponse(**get_org_usage(db, org_id))


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import ApiKey, Org, User

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(
        engine, tables=[Org.__table__, User.__table__, ApiKey.__table__]
    )

    db = TestingSession()
    try:
        db.add_all(
            [
                Org(id="org-1", name="Organization One"),
                Org(id="org-2", name="Organization Two"),
                User(
                    id="user-1",
                    email="analyst@example.com",
                    org_id="org-1",
                    role="analyst",
                    password_hash="not-returned-password-hash",
                ),
                User(
                    id="user-2",
                    email="viewer@example.com",
                    org_id="org-1",
                    role="viewer",
                    password_hash="not-returned-password-hash",
                ),
                User(
                    id="user-3",
                    email="admin@example.com",
                    org_id="org-2",
                    role="admin",
                    password_hash="not-returned-password-hash",
                ),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash="raw-secret-hash-value",
                    label="Primary",
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-2",
                    key_hash="second-secret-hash-value",
                    label="Primary",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/orgs/org-1/members")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["org_id"] == "org-1"
    assert payload["member_count"] == 2
    assert payload["by_role"] == {"analyst": 1, "viewer": 1}
    assert payload["api_key_count"] == 1
    assert "key_hash" not in response.text
    assert "raw-secret-hash-value" not in response.text

    usage_response = client.get("/api/orgs/org-1/usage")
    assert usage_response.status_code == 200, usage_response.text
    assert usage_response.json() == {"org_id": "org-1", "api_key_count": 1}
    assert "key_hash" not in usage_response.text
    assert "raw-secret-hash-value" not in usage_response.text

    print("PASS")


if __name__ == "__main__":
    _self_test()