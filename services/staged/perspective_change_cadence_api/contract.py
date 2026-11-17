from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

try:
    from .router import router as api_router
except ImportError:
    from .router import api_router


def _router_prefix() -> str:
    for route in api_router.routes:
        path = getattr(route, "path", "")
        if path == "/api/perspectives/change-cadence":
            return ""
        if path.endswith("/perspectives/change-cadence"):
            return "/api"
        if path.endswith("/change-cadence"):
            return "/api/perspectives"
    return ""


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    inside = now - timedelta(days=1)
    outside = now - timedelta(days=45)

    with testing_session() as session:
        session.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description TEXT,
                    facet_filters JSON NOT NULL,
                    created_by VARCHAR NOT NULL,
                    org_id VARCHAR,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    change_type VARCHAR NOT NULL,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

        session.add_all(
            [
                Perspective(
                    id="perspective-alpha",
                    name="Alpha",
                    description="Primary perspective",
                    facet_filters={},
                    created_by="contract-self-test",
                    created_at=now,
                    updated_at=now,
                ),
                Perspective(
                    id="perspective-beta",
                    name="Beta",
                    description="Secondary perspective",
                    facet_filters={},
                    created_by="contract-self-test",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-alpha",
                    server_id="server-1",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="high",
                    seen=True,
                    created_at=inside,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-alpha",
                    server_id="server-2",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="high",
                    seen=False,
                    created_at=inside,
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-beta",
                    server_id="server-3",
                    change_type="tier_change",
                    old_tier="medium",
                    new_tier="high",
                    seen=True,
                    created_at=outside,
                ),
                PerspectiveEvent(
                    id=4,
                    perspective_id="perspective-beta",
                    server_id="server-4",
                    change_type="tier_change",
                    old_tier="high",
                    new_tier="low",
                    seen=True,
                    created_at=outside,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(api_router, prefix=_router_prefix())
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/perspectives/change-cadence",
            params={"days": 30},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["total_events"] == 2

    alpha = next(
        item for item in payload["perspectives"] if item["perspective_id"] == "perspective-alpha"
    )
    assert alpha["name"] == "Alpha"
    assert alpha["event_count"] == 2
    assert alpha["by_change_type"]["tier_change"] == 2
    assert alpha["unseen_count"] == 1
    assert any(
        transition["old_tier"] == "low"
        and transition["new_tier"] == "high"
        and transition["count"] == 2
        for transition in alpha["tier_transitions"]
    )

    print("PASS")


if __name__ == "__main__":
    run_self_test()