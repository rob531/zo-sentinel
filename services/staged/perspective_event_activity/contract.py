"""Acceptance self-test for the perspective event activity service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, Perspective, PerspectiveEvent

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    perspective_one = "11111111-1111-4111-8111-111111111111"
    perspective_two = "22222222-2222-4222-8222-222222222222"
    orphan_perspective = "33333333-3333-4333-8333-333333333333"

    session = testing_session()
    try:
        session.add(
            Perspective(
                id=perspective_one,
                name="Primary perspective",
                org_id=None,
                created_by="44444444-4444-4444-8444-444444444444",
                description="",
                facet_filters={},
            )
        )
        session.add(
            Perspective(
                id=perspective_two,
                name="Secondary perspective",
                org_id=None,
                created_by="44444444-4444-4444-8444-444444444444",
                description="",
                facet_filters={},
            )
        )
        session.add(
            PerspectiveEvent(
                perspective_id=perspective_one,
                server_id="server-1",
                change_type="TIER_CHANGE",
                old_tier="CAUTION_LIMITED",
                new_tier="HIGH_RISK_ISOLATED",
                seen=False,
            )
        )
        session.add(
            PerspectiveEvent(
                perspective_id=perspective_two,
                server_id="server-2",
                change_type="TIER_CHANGE",
                old_tier="CAUTION_LIMITED",
                new_tier="HIGH_RISK_ISOLATED",
                seen=True,
            )
        )
        session.add(
            PerspectiveEvent(
                perspective_id=orphan_perspective,
                server_id="server-3",
                change_type="TIER_CHANGE",
                old_tier="HIGH_RISK_ISOLATED",
                new_tier="TRUSTED_GENERAL",
                seen=False,
            )
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    route_paths = [getattr(route, "path", "") for route in router.routes]
    include_prefix = "" if any(path.startswith("/api/") for path in route_paths) else "/api"
    app.include_router(router, prefix=include_prefix)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/perspectives/activity", params={"limit": 3})
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["limit"] == 3, body
        assert len(body["events"]) == 3, body

        orphan = next(
            event
            for event in body["events"]
            if event["perspective_id"] == orphan_perspective
        )
        assert orphan["perspective_name"] is None, body

        transitions = body["transitions"]
        caution_to_high = next(
            transition
            for transition in transitions
            if transition["old_tier"] == "CAUTION_LIMITED"
            and transition["new_tier"] == "HIGH_RISK_ISOLATED"
        )
        high_to_trusted = next(
            transition
            for transition in transitions
            if transition["old_tier"] == "HIGH_RISK_ISOLATED"
            and transition["new_tier"] == "TRUSTED_GENERAL"
        )
        assert caution_to_high["count"] == 2, body
        assert high_to_trusted["count"] == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)