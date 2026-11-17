from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(eng)
    McpLlmAxisScore.__table__.create(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TestingSession()
    s.add(
        McpServerRegistry(
            server_id="srv-conflict",
            name="Conflicting Server",
            risk_tier="HIGH",
            trust_score=0.25,
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv-excluded",
            name="Excluded Server",
            risk_tier="LOW",
            trust_score=0.95,
        )
    )

    scores = [
        McpLlmAxisScore(
            id=1,
            server_id="srv-conflict",
            axis_name="overall_risk",
            model_version="contract-test-v1",
            p_top=0.10,
            p_critical=0.05,
            escalated=False,
        ),
        McpLlmAxisScore(
            id=2,
            server_id="srv-conflict",
            axis_name="exploit_surface",
            model_version="contract-test-v1",
            p_top=0.95,
            p_critical=0.90,
            escalated=True,
        ),
        McpLlmAxisScore(
            id=3,
            server_id="srv-excluded",
            axis_name="overall_risk",
            model_version="contract-test-v1",
            p_top=0.20,
            p_critical=0.10,
            escalated=False,
        ),
        McpLlmAxisScore(
            id=4,
            server_id="srv-excluded",
            axis_name="exploit_surface",
            model_version="contract-test-v1",
            p_top=0.20,
            p_critical=0.10,
            escalated=False,
        ),
    ]
    s.add_all(scores)
    s.commit()
    s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    with TestClient(app) as client:
        response = client.get(
            "/api/scoring/axis-conflicts",
            params={"threshold": 0.7, "limit": 100},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["threshold"] == 0.7, body
        assert body["count"] == 1, body

        conflicts = body["conflicts"]
        assert len(conflicts) == 1, body
        conflict = conflicts[0]
        assert conflict["server_id"] == "srv-conflict", body
        assert conflict["name"] == "Conflicting Server", body
        assert conflict["overall_p_top"] == 0.10, body
        assert any(
            axis["axis_name"] == "exploit_surface" and axis["p_top"] == 0.95
            for axis in conflict["hot_axes"]
        ), body
        assert all(
            item["server_id"] != "srv-excluded" for item in conflicts
        ), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)