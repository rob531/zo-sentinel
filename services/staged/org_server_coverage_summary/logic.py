"""logic.py -- organisation MCP server coverage computation layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org, User


def get_org_server_coverage(db: Session) -> Dict[str, Any]:
    """Return the global MCP registry summary for every organisation.

    Registry rows are global rather than organisation-scoped.  The registry
    aggregate is therefore attached to each organisation alongside that
    organisation's user and role counts.
    """
    org_rows = db.execute(
        select(Org.id, Org.name).order_by(Org.id)
    ).all()

    role_rows = db.execute(
        select(User.org_id, User.role, func.count())
        .group_by(User.org_id, User.role)
    ).all()

    role_counts_by_org: Dict[str, Dict[str, int]] = {}
    for org_id, role, count in role_rows:
        org_key = str(org_id)
        role_key = role or "unknown"
        role_counts_by_org.setdefault(org_key, {})[role_key] = int(count)

    registry_total = int(
        db.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )

    avg_trust_score = db.execute(
        select(func.avg(McpServerRegistry.trust_score))
    ).scalar()
    average = float(avg_trust_score) if avg_trust_score is not None else None

    never_assessed = int(
        db.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_assessed.is_(None))
        ).scalar_one()
    )

    tier_rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    tier_counts: Dict[str, int] = {}
    for tier, count in tier_rows:
        tier_counts[tier or "unknown"] = int(count)

    orgs: List[Dict[str, Any]] = []
    for org_id, org_name in org_rows:
        org_key = str(org_id)
        roles = role_counts_by_org.get(org_key, {})
        orgs.append(
            {
                "org_id": org_key,
                "org_name": str(org_name),
                "user_count": sum(roles.values()),
                "role_counts": roles,
                "registry_total": registry_total,
                "tier_counts": tier_counts,
                "avg_trust_score": average,
                "never_assessed": never_assessed,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "orgs": orgs,
    }


def get_org_server_coverage_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the summary naming convention."""
    return get_org_server_coverage(db)


if __name__ == "__main__":
    from datetime import datetime as datetime_type

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    User.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)

    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )()

    testing_session.add_all(
        [
            Org(id="org-1", name="Organisation One"),
            Org(id="org-2", name="Organisation Two"),
            User(
                id="user-1",
                email="one@example.test",
                password_hash="hash-1",
                org_id="org-1",
                role="admin",
            ),
            User(
                id="user-2",
                email="two@example.test",
                password_hash="hash-2",
                org_id="org-1",
                role="member",
            ),
            User(
                id="user-3",
                email="three@example.test",
                password_hash="hash-3",
                org_id="org-2",
                role="admin",
            ),
            McpServerRegistry(
                server_id="server-1",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.90,
                last_assessed=datetime_type(2024, 1, 1, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="server-2",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.80,
                last_assessed=datetime_type(2024, 1, 2, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="server-3",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.70,
                last_assessed=None,
            ),
        ]
    )
    testing_session.commit()

    that_app = FastAPI()

    def override_get_session():
        yield testing_session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/orgs/server-coverage")
    def server_coverage(db: Session = Depends(get_session)):
        return get_org_server_coverage(db)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/server-coverage")
        assert response.status_code == 200
        payload = response.json()

    assert len(payload["orgs"]) == 2
    assert payload["orgs"][0]["user_count"] == 2
    assert payload["orgs"][1]["user_count"] == 1
    assert sum(payload["orgs"][0]["tier_counts"].values()) == 3
    assert payload["orgs"][0]["never_assessed"] == 1
    assert payload["orgs"][0]["avg_trust_score"] is not None
    assert payload["orgs"][1]["registry_total"] == 3

    print("PASS")