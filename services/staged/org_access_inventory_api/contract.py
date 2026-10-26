from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


router = APIRouter()
app = FastAPI()
app.include_router(router)


class RoleCounts(BaseModel):
    admin: int
    member: int
    other: int


class OrgInventory(BaseModel):
    org_id: str
    name: str
    created_at: str
    user_count: int
    api_key_count: int
    roles: RoleCounts
    users_without_keys: int


class InventoryTotals(BaseModel):
    orgs: int
    users: int
    api_keys: int


class AccessInventoryResponse(BaseModel):
    generated_at: str
    totals: InventoryTotals
    orgs: list[OrgInventory]
    unattached_users: int
    orgs_without_users: int


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get(
    "/api/orgs/access-inventory",
    response_model=AccessInventoryResponse,
)
def get_access_inventory(
    org_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> AccessInventoryResponse:
    org_rows = session.execute(
        text(
            """
            WITH scoped_orgs AS (
                SELECT o.id, o.name, o.created_at
                FROM orgs o
                WHERE (:org_id IS NULL OR CAST(o.id AS VARCHAR(255)) = CAST(:org_id AS VARCHAR(255)))
            ),
            scoped_users AS (
                SELECT u.id, u.org_id, u.role
                FROM users u
                JOIN scoped_orgs so ON so.id = u.org_id
            ),
            scoped_keys AS (
                SELECT k.id, k.org_id
                FROM api_keys k
                JOIN scoped_orgs so ON so.id = k.org_id
            )
            SELECT
                so.id AS org_id,
                so.name AS name,
                so.created_at AS created_at,
                COUNT(DISTINCT su.id) AS user_count,
                COUNT(DISTINCT sk.id) AS api_key_count,
                COUNT(DISTINCT CASE WHEN su.role = 'admin' THEN su.id END) AS admin_count,
                COUNT(DISTINCT CASE WHEN su.role = 'member' THEN su.id END) AS member_count,
                COUNT(DISTINCT CASE
                    WHEN su.id IS NOT NULL AND su.role NOT IN ('admin', 'member') THEN su.id
                END) AS other_count,
                COUNT(DISTINCT CASE
                    WHEN su.id IS NOT NULL AND NOT EXISTS (
                        SELECT 1
                        FROM api_keys ak
                        WHERE ak.org_id = so.id
                    ) THEN su.id
                END) AS users_without_keys
            FROM scoped_orgs so
            LEFT JOIN scoped_users su ON su.org_id = so.id
            LEFT JOIN scoped_keys sk ON sk.org_id = so.id
            GROUP BY so.id, so.name, so.created_at
            ORDER BY so.id
            """
        ),
        {"org_id": org_id},
    ).mappings()

    orgs: list[OrgInventory] = []
    for row in org_rows:
        user_count = int(row["user_count"] or 0)
        api_key_count = int(row["api_key_count"] or 0)
        admin_count = int(row["admin_count"] or 0)
        member_count = int(row["member_count"] or 0)
        other_count = int(row["other_count"] or 0)
        orgs.append(
            OrgInventory(
                org_id=str(row["org_id"]),
                name=str(row["name"]),
                created_at=_iso(row["created_at"]),
                user_count=user_count,
                api_key_count=api_key_count,
                roles=RoleCounts(
                    admin=admin_count,
                    member=member_count,
                    other=other_count,
                ),
                users_without_keys=int(row["users_without_keys"] or 0),
            )
        )

    if org_id is None:
        users_total = int(
            session.execute(
                text("SELECT COUNT(DISTINCT u.id) FROM users u"),
            ).scalar_one()
        )
        api_keys_total = int(
            session.execute(
                text("SELECT COUNT(DISTINCT k.id) FROM api_keys k"),
            ).scalar_one()
        )
    else:
        users_total = int(
            session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users u
                    WHERE CAST(u.org_id AS VARCHAR(255)) = CAST(:org_id AS VARCHAR(255))
                    """
                ),
                {"org_id": org_id},
            ).scalar_one()
        )
        api_keys_total = int(
            session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT k.id)
                    FROM api_keys k
                    WHERE CAST(k.org_id AS VARCHAR(255)) = CAST(:org_id AS VARCHAR(255))
                    """
                ),
                {"org_id": org_id},
            ).scalar_one()
        )

    unattached_users = int(
        session.execute(
            text(
                """
                SELECT COUNT(DISTINCT u.id)
                FROM users u
                LEFT JOIN orgs o ON o.id = u.org_id
                WHERE u.org_id IS NULL OR o.id IS NULL
                """
            )
        ).scalar_one()
    )

    orgs_without_users = sum(1 for org in orgs if org.user_count == 0)

    return AccessInventoryResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        totals=InventoryTotals(
            orgs=len(orgs),
            users=users_total,
            api_keys=api_keys_total,
        ),
        orgs=orgs,
        unattached_users=unattached_users,
        orgs_without_users=orgs_without_users,
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    test_session = session_factory()
    now = datetime.now(timezone.utc)

    org_one = Org(
        id="org-one",
        name="Organization One",
        created_at=now,
    )
    org_two = Org(
        id="org-two",
        name="Organization Two",
        created_at=now,
    )
    test_session.add_all([org_one, org_two])
    test_session.flush()

    test_session.add_all(
        [
            User(
                id="user-one",
                email="one@example.test",
                org_id="org-one",
                role="admin",
                clerk_id="clerk-one",
                created_at=now,
                password_hash="test-password-value",
            ),
            User(
                id="user-two",
                email="two@example.test",
                org_id="org-one",
                role="viewer",
                clerk_id="clerk-two",
                created_at=now,
                password_hash="test-password-value",
            ),
            User(
                id="user-three",
                email="three@example.test",
                org_id="org-two",
                role="member",
                clerk_id="clerk-three",
                created_at=now,
                password_hash="test-password-value",
            ),
        ]
    )
    test_session.add_all(
        [
            ApiKey(
                id="key-one",
                org_id="org-one",
                label="Primary",
                created_at=now,
                key_hash="test-key-value",
            ),
            ApiKey(
                id="key-two",
                org_id="org-one",
                label="Secondary",
                created_at=now,
                key_hash="test-key-value",
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/access-inventory")
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["orgs"] == 2
    assert body["totals"]["users"] == 3
    assert body["totals"]["api_keys"] == 2

    known_org = next(org for org in body["orgs"] if org["org_id"] == "org-one")
    assert known_org["user_count"] == 2
    assert known_org["roles"]["other"] == 1
    assert "password_hash" not in response.text
    assert "key_hash" not in response.text

    print("PASS")
    test_session.close()
    engine.dispose()