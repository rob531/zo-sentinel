from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


class AccessInventoryTotals(BaseModel):
    orgs: int
    users: int
    api_keys: int


class AccessRoleCounts(BaseModel):
    admin: int
    member: int
    other: int


class OrgAccessInventoryItem(BaseModel):
    org_id: str
    name: str
    created_at: str
    user_count: int
    api_key_count: int
    roles: AccessRoleCounts
    users_without_keys: int


class AccessInventoryResponse(BaseModel):
    generated_at: str
    totals: AccessInventoryTotals
    orgs: list[OrgAccessInventoryItem]
    unattached_users: int
    orgs_without_users: int


def _iso8601(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    return str(value)


def compute_access_inventory(
    session: Session,
    org_id: Optional[str] = None,
) -> AccessInventoryResponse:
    statement = (
        select(
            Org.id,
            Org.name,
            Org.created_at,
            func.count(distinct(User.id)).label("user_count"),
            func.count(distinct(ApiKey.id)).label("api_key_count"),
            func.count(
                distinct(case((User.role == "admin", User.id), else_=None))
            ).label("admin_count"),
            func.count(
                distinct(case((User.role == "member", User.id), else_=None))
            ).label("member_count"),
            func.count(
                distinct(
                    case(
                        (
                            (User.role != "admin") & (User.role != "member"),
                            User.id,
                        ),
                        else_=None,
                    )
                )
            ).label("other_count"),
        )
        .select_from(Org)
        .outerjoin(User, User.org_id == Org.id)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .group_by(Org.id, Org.name, Org.created_at)
        .order_by(Org.name, Org.id)
    )

    if org_id is not None:
        statement = statement.where(Org.id == org_id)

    rows = session.execute(statement).all()

    org_items: list[OrgAccessInventoryItem] = []
    total_users = 0
    total_api_keys = 0
    orgs_without_users = 0

    for row in rows:
        user_count = int(row.user_count or 0)
        api_key_count = int(row.api_key_count or 0)
        admin_count = int(row.admin_count or 0)
        member_count = int(row.member_count or 0)
        other_count = int(row.other_count or 0)

        total_users += user_count
        total_api_keys += api_key_count
        if user_count == 0:
            orgs_without_users += 1

        org_items.append(
            OrgAccessInventoryItem(
                org_id=str(row.id),
                name=str(row.name),
                created_at=_iso8601(row.created_at),
                user_count=user_count,
                api_key_count=api_key_count,
                roles=AccessRoleCounts(
                    admin=admin_count,
                    member=member_count,
                    other=other_count,
                ),
                users_without_keys=user_count if api_key_count == 0 else 0,
            )
        )

    unattached_users = int(
        session.execute(
            select(func.count(distinct(User.id))).where(User.org_id.is_(None))
        ).scalar_one()
        or 0
    )
    if org_id is not None:
        unattached_users = 0

    return AccessInventoryResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        totals=AccessInventoryTotals(
            orgs=len(org_items),
            users=total_users,
            api_keys=total_api_keys,
        ),
        orgs=org_items,
        unattached_users=unattached_users,
        orgs_without_users=orgs_without_users,
    )


def build_access_inventory(
    session: Session = Depends(get_session),
    org_id: Optional[str] = None,
) -> AccessInventoryResponse:
    return compute_access_inventory(session=session, org_id=org_id)


get_access_inventory = build_access_inventory
get_org_access_inventory = build_access_inventory


def run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            Org(id="org-alpha", name="Alpha Org", created_at=now),
            Org(id="org-beta", name="Beta Org", created_at=now),
            User(
                id="user-1",
                email="admin@example.test",
                org_id="org-alpha",
                role="admin",
                created_at=now,
                clerk_id="clerk-1",
                password_hash="private-password-value",
            ),
            User(
                id="user-2",
                email="member@example.test",
                org_id="org-alpha",
                role="member",
                created_at=now,
                clerk_id="clerk-2",
                password_hash="private-password-value",
            ),
            User(
                id="user-3",
                email="viewer@example.test",
                org_id="org-beta",
                role="viewer",
                created_at=now,
                clerk_id="clerk-3",
                password_hash="private-password-value",
            ),
            ApiKey(
                id="key-1",
                org_id="org-alpha",
                label="Primary",
                created_at=now,
                key_hash="private-key-value",
            ),
            ApiKey(
                id="key-2",
                org_id="org-alpha",
                label="Secondary",
                created_at=now,
                key_hash="private-key-value",
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/orgs/access-inventory", response_model=AccessInventoryResponse)
    def access_inventory_endpoint(
        org_id: Optional[str] = None,
        session: Session = Depends(get_session),
    ) -> AccessInventoryResponse:
        return compute_access_inventory(session=session, org_id=org_id)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/access-inventory")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["orgs"] == 2
        assert body["totals"]["users"] == 3
        assert body["totals"]["api_keys"] == 2
        alpha = next(org for org in body["orgs"] if org["org_id"] == "org-alpha")
        assert alpha["user_count"] == 2
        assert alpha["roles"]["other"] == 0
        beta = next(org for org in body["orgs"] if org["org_id"] == "org-beta")
        assert beta["roles"]["other"] == 1
        serialized = response.text
        assert "password_hash" not in serialized
        assert "key_hash" not in serialized
        assert "private-password-value" not in serialized
        assert "private-key-value" not in serialized

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run_self_test()