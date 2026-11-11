from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, McpServerRegistry, Org, User


TIER_KEYS = (
    "TRUSTED_GENERAL",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "INSUFFICIENT",
)


def get_org_server_scope(
    session: Session = Depends(get_session),
    org_id: Optional[str] = None,
) -> dict:
    """Return per-organization usage and registry risk-tier counts.

    McpServerRegistry has no authoritative organization foreign-key column.
    registry_source and meta do not define a documented organization relation,
    and registry membership cannot be inferred through api_keys. Consequently,
    server counts and tier distributions remain zero rather than inventing a
    registry-to-organization join.
    """
    statement = (
        select(
            Org.id.label("org_id"),
            Org.name.label("org_name"),
            func.count(distinct(User.id)).label("members"),
            func.count(distinct(ApiKey.id)).label("api_keys"),
        )
        .outerjoin(User, User.org_id == Org.id)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .group_by(Org.id, Org.name)
        .order_by(Org.name, Org.id)
    )
    if org_id is not None:
        statement = statement.where(Org.id == org_id)

    rows = session.execute(statement).all()
    organizations = []

    for row in rows:
        tiers = {tier: 0 for tier in TIER_KEYS}
        tiers["other"] = 0
        organizations.append(
            {
                "org_id": str(row.org_id),
                "org_name": str(row.org_name),
                "members": int(row.members or 0),
                "api_keys": int(row.api_keys or 0),
                "servers": 0,
                "tiers": tiers,
            }
        )

    return {
        "orgs": organizations,
        "total_orgs": len(organizations),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    for model in (Org, User, ApiKey, McpServerRegistry):
        model.__table__.create(test_engine, checkfirst=True)

    that_app = FastAPI()

    @that_app.get("/api/orgs/server-scope")
    def org_server_scope(session: Session = Depends(get_session)) -> dict:
        return get_org_server_scope(session)

    def override_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    with TestSession() as session:
        session.add_all(
            [
                Org(
                    id="00000000-0000-0000-0000-000000000001",
                    name="Alpha Org",
                ),
                Org(
                    id="00000000-0000-0000-0000-000000000002",
                    name="Beta Org",
                ),
                User(
                    id="00000000-0000-0000-0000-000000000011",
                    email="alpha-one@example.com",
                    org_id="00000000-0000-0000-0000-000000000001",
                    role="admin",
                    password_hash="test",
                ),
                User(
                    id="00000000-0000-0000-0000-000000000012",
                    email="alpha-two@example.com",
                    org_id="00000000-0000-0000-0000-000000000001",
                    role="member",
                    password_hash="test",
                ),
                User(
                    id="00000000-0000-0000-0000-000000000013",
                    email="beta-one@example.com",
                    org_id="00000000-0000-0000-0000-000000000002",
                    role="admin",
                    password_hash="test",
                ),
                ApiKey(
                    id="00000000-0000-0000-0000-000000000021",
                    org_id="00000000-0000-0000-0000-000000000001",
                    label="alpha-key",
                    key_hash="test-key-1",
                ),
                ApiKey(
                    id="00000000-0000-0000-0000-000000000022",
                    org_id="00000000-0000-0000-0000-000000000002",
                    label="beta-key",
                    key_hash="test-key-2",
                ),
            ]
        )
        session.commit()

    response = TestClient(that_app).get("/api/orgs/server-scope")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert payload["total_orgs"] == 2

    by_name = {organization["org_name"]: organization for organization in payload["orgs"]}
    assert by_name["Alpha Org"]["members"] == 2
    assert by_name["Alpha Org"]["api_keys"] == 1
    assert by_name["Beta Org"]["members"] == 1
    assert by_name["Beta Org"]["api_keys"] == 1

    for organization in payload["orgs"]:
        assert organization["servers"] == 0
        assert set(organization["tiers"]) == {
            "TRUSTED_GENERAL",
            "ENTERPRISE_CONTROLLED",
            "CAUTION_LIMITED",
            "HIGH_RISK_ISOLATED",
            "INSUFFICIENT",
            "other",
        }
        assert all(count == 0 for count in organization["tiers"].values())

    print("PASS")


if __name__ == "__main__":
    _self_test()