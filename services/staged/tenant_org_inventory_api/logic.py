from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_tenant_inventory(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
                o.id AS org_id,
                o.name AS org_name,
                o.created_at AS org_created_at,
                u.role AS role,
                COUNT(DISTINCT u.id) AS user_count,
                COUNT(DISTINCT ak.id) AS api_key_count,
                MIN(ak.created_at) AS oldest_api_key_created_at,
                MAX(ak.created_at) AS newest_api_key_created_at
            FROM orgs AS o
            LEFT JOIN users AS u
                ON u.org_id = o.id
            LEFT JOIN api_keys AS ak
                ON ak.org_id = o.id
            GROUP BY
                o.id,
                o.name,
                o.created_at,
                u.role
            ORDER BY o.id
            """
        )
    ).mappings()

    inventories: dict[str, dict[str, Any]] = {}

    for row in rows:
        org_id = str(row["org_id"])
        inventory = inventories.setdefault(
            org_id,
            {
                "org_id": org_id,
                "org_name": str(row["org_name"]),
                "created_at": _as_iso(row["org_created_at"]),
                "user_count": 0,
                "api_key_count": int(row["api_key_count"] or 0),
                "role_breakdown": {},
                "oldest_api_key_created_at": _as_iso(
                    row["oldest_api_key_created_at"]
                ),
                "newest_api_key_created_at": _as_iso(
                    row["newest_api_key_created_at"]
                ),
            },
        )

        role = row["role"]
        user_count = int(row["user_count"] or 0)
        if role is not None:
            inventory["role_breakdown"][str(role)] = user_count
        inventory["user_count"] += user_count

    for inventory in inventories.values():
        inventory["role_breakdown"] = {
            role: count
            for role, count in sorted(inventory["role_breakdown"].items())
        }

    return list(inventories.values())


get_inventory = get_tenant_inventory
get_tenant_org_inventory = get_tenant_inventory
get_tenant_inventory_data = get_tenant_inventory


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    testing_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=testing_engine, autoflush=False)

    Org.__table__.create(testing_engine)
    User.__table__.create(testing_engine)
    ApiKey.__table__.create(testing_engine)

    session = TestingSession()
    session.add_all(
        [
            Org(id="org-1", name="Organization One"),
            Org(id="org-2", name="Organization Two"),
            User(
                id="user-1",
                org_id="org-1",
                role="admin",
                email="user1@example.test",
                password_hash="not-returned",
            ),
            User(
                id="user-2",
                org_id="org-1",
                role="member",
                email="user2@example.test",
                password_hash="not-returned",
            ),
            User(
                id="user-3",
                org_id="org-2",
                role="member",
                email="user3@example.test",
                password_hash="not-returned",
            ),
            ApiKey(
                id="key-1",
                org_id="org-1",
                label="Primary",
                key_hash="not-returned",
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                label="Secondary",
                key_hash="not-returned",
            ),
        ]
    )
    session.commit()
    session.close()

    testing_app = FastAPI()

    def override_get_session():
        with TestingSession() as testing_session:
            yield testing_session

    testing_app.dependency_overrides[get_session] = override_get_session

    @testing_app.get("/api/tenants/inventory")
    def inventory(session: Session = Depends(get_session)):
        orgs = get_tenant_inventory(session)
        return {"orgs": orgs, "total_orgs": len(orgs)}

    response = TestClient(testing_app).get("/api/tenants/inventory")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_orgs"] == 2
    assert len(payload["orgs"]) == 2

    organization_one = next(
        org for org in payload["orgs"] if org["org_id"] == "org-1"
    )
    assert organization_one["user_count"] == 2
    assert sum(organization_one["role_breakdown"].values()) == 2

    response_text = response.text.lower()
    assert "hash" not in response_text

    print("PASS")