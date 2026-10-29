from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


class RoleCounts(BaseModel):
    admin: int = 0
    analyst: int = 0
    viewer: int = 0


class ApiKeyAgeCounts(BaseModel):
    older_than_90: int = 0
    newer_or_equal_90: int = 0


class OrgMemberRollup(BaseModel):
    org_id: str
    name: str
    created_at: Optional[str]
    members: int
    by_role: RoleCounts
    distinct_roles: int
    api_keys: int
    api_key_ages_days: ApiKeyAgeCounts


class MemberRollupResponse(BaseModel):
    orgs: List[OrgMemberRollup]
    total_orgs: int
    orphaned_users: int


router = APIRouter(prefix="/api")


def _timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _older_than_cutoff(value: object, cutoff: datetime) -> bool:
    if not isinstance(value, datetime):
        return False

    comparable_cutoff = cutoff
    comparable_value = value
    if comparable_value.tzinfo is not None and comparable_cutoff.tzinfo is None:
        comparable_value = comparable_value.replace(tzinfo=None)
    elif comparable_value.tzinfo is None and comparable_cutoff.tzinfo is not None:
        comparable_cutoff = comparable_cutoff.replace(tzinfo=None)

    return comparable_value < comparable_cutoff


def build_member_rollup(session: Session) -> MemberRollupResponse:
    org_rows = session.execute(select(Org).order_by(Org.id)).scalars().all()
    user_rows = session.execute(select(User)).scalars().all()
    api_key_rows = session.execute(select(ApiKey)).scalars().all()

    users_by_org: Dict[object, List[User]] = {}
    for user in user_rows:
        users_by_org.setdefault(user.org_id, []).append(user)

    keys_by_org: Dict[object, List[ApiKey]] = {}
    for api_key in api_key_rows:
        keys_by_org.setdefault(api_key.org_id, []).append(api_key)

    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    orgs: List[OrgMemberRollup] = []

    for org in org_rows:
        members = users_by_org.get(org.id, [])
        role_counts = {"admin": 0, "analyst": 0, "viewer": 0}
        distinct_roles = set()

        for member in members:
            if member.role is not None:
                distinct_roles.add(member.role)
            if member.role in role_counts:
                role_counts[member.role] += 1

        older_than_90 = 0
        newer_or_equal_90 = 0
        for api_key in keys_by_org.get(org.id, []):
            if _older_than_cutoff(api_key.created_at, cutoff):
                older_than_90 += 1
            elif isinstance(api_key.created_at, datetime):
                newer_or_equal_90 += 1

        orgs.append(
            OrgMemberRollup(
                org_id=str(org.id),
                name=org.name,
                created_at=_timestamp(org.created_at),
                members=len(members),
                by_role=RoleCounts(**role_counts),
                distinct_roles=len(distinct_roles),
                api_keys=len(keys_by_org.get(org.id, [])),
                api_key_ages_days=ApiKeyAgeCounts(
                    older_than_90=older_than_90,
                    newer_or_equal_90=newer_or_equal_90,
                ),
            )
        )

    orphaned_users = session.execute(
        select(func.count())
        .select_from(User)
        .outerjoin(Org, User.org_id == Org.id)
        .where(Org.id.is_(None))
    ).scalar_one()

    return MemberRollupResponse(
        orgs=orgs,
        total_orgs=len(org_rows),
        orphaned_users=int(orphaned_users),
    )


@router.get("/orgs/member-rollup", response_model=MemberRollupResponse)
def get_org_member_rollup(
    session: Session = Depends(get_session),
) -> MemberRollupResponse:
    return build_member_rollup(session)


def _seed_database(session: Session) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    recent = now - timedelta(days=10)

    session.execute(
        text(
            "INSERT INTO orgs (id, name, created_at) "
            "VALUES (:id, :name, :created_at)"
        ),
        [
            {"id": 1, "name": "First Org", "created_at": now},
            {"id": 2, "name": "Second Org", "created_at": now},
        ],
    )
    session.execute(
        text(
            "INSERT INTO users "
            "(id, email, org_id, role, created_at, password_hash, "
            "clerk_id, clerk_created_at, clerk_synced_via) "
            "VALUES (:id, :email, :org_id, :role, :created_at, "
            ":password_hash, :clerk_id, :clerk_created_at, :clerk_synced_via)"
        ),
        [
            {
                "id": 1,
                "email": "admin@example.test",
                "org_id": 1,
                "role": "admin",
                "created_at": now,
                "password_hash": "contract-test",
                "clerk_id": "clerk_admin",
                "clerk_created_at": now,
                "clerk_synced_via": "contract_test",
            },
            {
                "id": 2,
                "email": "viewer@example.test",
                "org_id": 1,
                "role": "viewer",
                "created_at": now,
                "password_hash": "contract-test",
                "clerk_id": "clerk_viewer",
                "clerk_created_at": now,
                "clerk_synced_via": "contract_test",
            },
            {
                "id": 3,
                "email": "orphan@example.test",
                "org_id": 999,
                "role": "analyst",
                "created_at": now,
                "password_hash": "contract-test",
                "clerk_id": "clerk_orphan",
                "clerk_created_at": now,
                "clerk_synced_via": "contract_test",
            },
        ],
    )
    session.execute(
        text(
            "INSERT INTO api_keys (id, org_id, key_hash, label, created_at) "
            "VALUES (:id, :org_id, :key_hash, :label, :created_at)"
        ),
        [
            {
                "id": 1,
                "org_id": 1,
                "key_hash": "contract-test-hash-old",
                "label": "old",
                "created_at": old,
            },
            {
                "id": 2,
                "org_id": 1,
                "key_hash": "contract-test-hash-recent",
                "label": "recent",
                "created_at": recent,
            },
        ],
    )
    session.commit()


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as session:
        _seed_database(session)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/member-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_orgs"] == 2
        assert payload["orgs"][0]["members"] == 2
        assert payload["orphaned_users"] == 1

    print("PASS")


if __name__ == "__main__":
    main()