"""HTTP surface for the organization tenant roster service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_roster


router = APIRouter(prefix="/api", tags=["org_tenant_roster"])


class OrgSummary(BaseModel):
    id: str
    name: str
    created_at: datetime


class RosterUser(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime
    has_clerk_identity: bool


class RosterApiKey(BaseModel):
    id: str
    label: str
    created_at: datetime
    key_hash_prefix: str


class RosterResponse(BaseModel):
    org: OrgSummary
    users: list[RosterUser]
    api_keys: list[RosterApiKey]


@router.get("/orgs/{org_id}/roster", response_model=RosterResponse)
def get_roster(
    org_id: str, db: Session = Depends(get_session)
) -> RosterResponse | JSONResponse:
    roster: Any = get_org_roster(db, org_id)
    if roster is None:
        return JSONResponse(status_code=404, content={"error": "org_not_found"})
    return RosterResponse.model_validate(roster)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ApiKey, Org, User

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine)
    User.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add(Org(id="org_1", name="Test Organization"))
        session.add(
            User(
                id="user_1",
                email="member@example.com",
                org_id="org_1",
                role="member",
                password_hash="password-hash-1",
                clerk_id="clerk_1",
            )
        )
        session.add(
            User(
                id="user_2",
                email="owner@example.com",
                org_id="org_1",
                role="owner",
                password_hash="password-hash-2",
            )
        )
        session.add(
            ApiKey(
                id="key_1",
                org_id="org_1",
                key_hash="0123456789abcdef",
                label="Primary",
            )
        )
        session.add(
            ApiKey(
                id="key_2",
                org_id="org_1",
                key_hash="fedcba9876543210",
                label="Secondary",
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/org_1/roster")
        assert response.status_code == 200
        payload = response.json()
        assert {user["id"] for user in payload["users"]} == {"user_1", "user_2"}
        assert all(
            len(api_key["key_hash_prefix"]) <= 8
            and "key_hash" not in api_key
            for api_key in payload["api_keys"]
        )

        response = client.get("/api/orgs/nope/roster")
        assert response.status_code == 404
        assert response.json() == {"error": "org_not_found"}

    print("PASS")