from collections import Counter
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org, User


def _normalise(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def get_org_tier_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return server tier and verdict counts grouped by organisation."""
    rows = session.execute(
        select(
            Org.id,
            Org.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
        .select_from(Org)
        .outerjoin(User, User.org_id == Org.id)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == User.id,
        )
    ).all()

    grouped: Dict[Any, Dict[str, Any]] = {}
    for org_id, org_name, risk_tier, verdict in rows:
        if org_id not in grouped:
            grouped[org_id] = {
                "org_id": org_id,
                "name": org_name,
                "tier_counts": {},
                "verdict_breakdown": {},
                "total_servers": 0,
                "high_risk_count": 0,
            }

        org = grouped[org_id]
        if risk_tier is None and verdict is None:
            continue

        tier = _normalise(risk_tier)
        verdict_name = _normalise(verdict)
        org["tier_counts"][tier] = org["tier_counts"].get(tier, 0) + 1
        org["verdict_breakdown"][verdict_name] = (
            org["verdict_breakdown"].get(verdict_name, 0) + 1
        )
        org["total_servers"] += 1
        if tier.lower() == "high":
            org["high_risk_count"] += 1

    orgs: List[Dict[str, Any]] = list(grouped.values())
    return {"orgs": orgs}


def get_tier_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_org_tier_distribution(session)


def compute_org_tier_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_org_tier_distribution(session)


def get_organization_tier_distribution(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_tier_distribution(session)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine, checkfirst=True)
    User.__table__.create(engine, checkfirst=True)
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                Org(id="org-1", name="Organisation One"),
                Org(id="org-2", name="Organisation Two"),
            ]
        )
        session.flush()

        users = [
            User(
                id="user-1",
                email="one@example.test",
                password_hash="test",
                org_id="org-1",
            ),
            User(
                id="user-2",
                email="two@example.test",
                password_hash="test",
                org_id="org-1",
            ),
            User(
                id="user-3",
                email="three@example.test",
                password_hash="test",
                org_id="org-2",
            ),
            User(
                id="user-4",
                email="four@example.test",
                password_hash="test",
                org_id="org-2",
            ),
        ]
        session.add_all(users)
        session.flush()

        session.add_all(
            [
                McpServerRegistry(
                    server_id="user-1",
                    name="Server One",
                    risk_tier="high",
                    verdict="malicious",
                ),
                McpServerRegistry(
                    server_id="user-2",
                    name="Server Two",
                    risk_tier="medium",
                    verdict="suspicious",
                ),
                McpServerRegistry(
                    server_id="user-3",
                    name="Server Three",
                    risk_tier="low",
                    verdict="safe",
                ),
                McpServerRegistry(
                    server_id="user-4",
                    name="Server Four",
                    risk_tier="high",
                    verdict="malicious",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/orgs/tier-distribution")
    def tier_distribution(session: Session = Depends(get_session)):
        return get_org_tier_distribution(session)

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/tier-distribution")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert payload["orgs"][0]["tier_counts"]["high"] == 1
    print("PASS")