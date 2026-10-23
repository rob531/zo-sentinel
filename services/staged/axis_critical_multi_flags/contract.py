from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.models import McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Server One", risk_tier="HIGH"),
                McpServerRegistry(server_id="server-2", name="Server Two", risk_tier="MEDIUM"),
                McpServerRegistry(server_id="server-3", name="Server Three", risk_tier="LOW"),
            ]
        )
        axis_names = (
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        )
        critical_axes = {"overall_risk", "exploit_surface"}
        scores = []
        score_id = 1
        for axis_name in axis_names:
            scores.append(
                McpLlmAxisScore(
                    id=score_id,
                    server_id="server-1",
                    axis_name=axis_name,
                    label="CRITICAL" if axis_name in critical_axes else "LOW",
                    p_critical=0.9 if axis_name in critical_axes else 0.1,
                    model_version="contract-test",
                )
            )
            score_id += 1
        scores.append(
            McpLlmAxisScore(
                id=score_id,
                server_id="server-2",
                axis_name="overall_risk",
                label="CRITICAL",
                p_critical=0.8,
                model_version="contract-test",
            )
        )
        score_id += 1
        scores.append(
            McpLlmAxisScore(
                id=score_id,
                server_id="server-3",
                axis_name="overall_risk",
                label="LOW",
                p_critical=0.1,
                model_version="contract-test",
            )
        )
        session.add_all(scores)
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/critical/multi-flag")
        assert response.status_code == 200, response.text
        body = response.json()
        servers = body["servers"]
        assert sum(server["critical_count"] == 2 for server in servers) == 1, body
        assert len(servers) == 1, body
        assert servers[0]["server_id"] == "server-1", body
        assert len(servers[0]["critical_axes"]) == 2, body

    engine.dispose()
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)