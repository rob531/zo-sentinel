"""contract.py -- acceptance self-test for the axis coverage gap service.

Run: python -m services.staged.axis_coverage_gap.contract
"""
from __future__ import annotations

import sys


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


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

    session = TestingSession()
    session.add(McpServerRegistry(server_id="srv-full", name="Fully scored", risk_tier="LOW"))
    session.add(McpServerRegistry(server_id="srv-partial", name="Partially scored", risk_tier="HIGH"))
    session.add(McpServerRegistry(server_id="srv-none", name="Unscored", risk_tier="HIGH"))

    score_id = 1
    for axis_name in CANONICAL_AXES:
        session.add(
            McpLlmAxisScore(
                id=score_id,
                server_id="srv-full",
                axis_name=axis_name,
                model_version="contract-test",
                p_top=0.1,
            )
        )
        score_id += 1

    for axis_name in CANONICAL_AXES[:3]:
        session.add(
            McpLlmAxisScore(
                id=score_id,
                server_id="srv-partial",
                axis_name=axis_name,
                model_version="contract-test",
                p_top=0.2,
            )
        )
        score_id += 1

    session.commit()
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
    response = client.get("/api/scores/axis-coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_servers"] == 3, body

    gap_servers = body["gap_servers"]
    partial = next((server for server in gap_servers if server["server_id"] == "srv-partial"), None)
    assert partial is not None, body
    assert partial["missing_count"] == 4, partial
    assert len(partial["missing_axes"]) == 4, partial

    assert all(server["server_id"] != "srv-full" for server in gap_servers), body
    assert any(server["server_id"] == "srv-none" for server in gap_servers), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)