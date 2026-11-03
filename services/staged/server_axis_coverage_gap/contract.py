"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Run:  python -m services.staged.server_axis_coverage_gap.contract
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-complete",
                    name="Complete server",
                    registry_source="test",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="srv-gap",
                    name="Gap server",
                    registry_source="test",
                    risk_tier="LOW",
                ),
            ]
        )
        session.flush()

        axes = (
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=index,
                    server_id="srv-complete",
                    axis_name=axis,
                    model_version="test-model",
                    scored_at=recent,
                )
                for index, axis in enumerate(axes, start=1)
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=index + len(axes),
                    server_id="srv-gap",
                    axis_name=axis,
                    model_version="test-model",
                    scored_at=recent,
                )
                for index, axis in enumerate(axes[:2], start=1)
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/scoring/coverage-gap")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["gap_count"] == 1, body
    assert body["total_servers"] == 2, body

    gaps = body["gaps"]
    assert len(gaps) == 1, body
    gap = gaps[0]
    assert gap["server_id"] == "srv-gap", body
    assert len(gap["missing_axes"]) == 4, body
    assert all(server_id != "srv-complete" for server_id in [g["server_id"] for g in gaps]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)