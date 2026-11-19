"""Acceptance self-test for the axis regeneration cost estimate API."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta


AXES = (
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
    scored_at = datetime.utcnow() - timedelta(days=2)
    score_id = 1

    for server_number in range(1, 4):
        session.add(
            McpServerRegistry(
                server_id=f"srv{server_number}",
                name=f"Server {server_number}",
                risk_tier="HIGH" if server_number < 3 else "LOW",
                registry_source="acceptance",
            )
        )

        for axis_name in ("overall_risk", "auth_strength"):
            session.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id=f"srv{server_number}",
                    axis_name=axis_name,
                    p_top=0.2,
                    p_critical=0.3,
                    p_danger=0.5,
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="test-model",
                    adapter_sha256="0" * 64,
                    scored_at=scored_at,
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
    response = client.get("/api/scoring/axis-regeneration-cost", params={"hours": 24})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["hours"] == 24, body
    assert len(body["by_axis"]) == 7, body
    assert {entry["axis_name"] for entry in body["by_axis"]} == set(AXES), body
    assert any(entry["missing_count"] >= 1 for entry in body["by_axis"]), body

    summed_counts = sum(
        entry["missing_count"] + entry["stale_count"] for entry in body["by_axis"]
    )
    assert body["estimate"]["rows_to_write"] == summed_counts, body
    assert body["total_backlog"] == summed_counts, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)