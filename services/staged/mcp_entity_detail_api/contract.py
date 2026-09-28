from __future__ import annotations

import sys


def run() -> bool:
    import json
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    for model in (McpServerRegistry, McpLlmAxisScore, McpScoreDispute):
        model.__table__.create(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE mcp_server_registry ADD COLUMN meta TEXT"
        )
        connection.exec_driver_sql(
            "ALTER TABLE mcp_server_registry ADD COLUMN created_at DATETIME"
        )

    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime(2024, 1, 15, 10, 30, 0)
    servers = (
        ("srv1", "Alpha Server", "https://alpha.example.test", "Primary test server"),
        ("srv2", "Beta Server", "https://beta.example.test", "Secondary test server"),
        ("srv3", "Gamma Server", "https://gamma.example.test", "Tertiary test server"),
    )

    with TestingSession() as db:
        for server_id, name, url, description in servers:
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    url=url,
                    description=description,
                    trust_score=0.85,
                    verdict="approved",
                    confidence=0.92,
                    risk_tier="low",
                    last_scanned=now,
                    scan_count=10,
                    meta=json.dumps({"contract_test": True}),
                    first_seen=now,
                )
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

        for index, axis_name in enumerate(axis_names):
            db.add(
                McpLlmAxisScore(
                    id=index + 1,
                    server_id="srv1",
                    axis_name=axis_name,
                    label=f"Label {index}",
                    label_index=index,
                    p_top=0.1 + index * 0.01,
                    p_critical=0.2 + index * 0.01,
                    p_danger=0.3 + index * 0.01,
                    escalated=index < 2,
                    model_version="contract-test-v1",
                )
            )

        db.add_all(
            (
                McpScoreDispute(
                    id=1,
                    server_id="srv1",
                    submitted_by="contract-test",
                    proposed_overall_risk="0.2",
                    reason_category="contract_test",
                    explanation="First open test dispute",
                    status="open",
                    created_at=datetime(2024, 1, 10, 9, 0, 0),
                ),
                McpScoreDispute(
                    id=2,
                    server_id="srv1",
                    submitted_by="contract-test",
                    proposed_overall_risk="0.3",
                    reason_category="contract_test",
                    explanation="Second open test dispute",
                    status="open",
                    created_at=datetime(2024, 1, 12, 14, 30, 0),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="srv1",
                    submitted_by="contract-test",
                    proposed_overall_risk="0.4",
                    reason_category="contract_test",
                    explanation="Resolved test dispute",
                    status="resolved",
                    created_at=datetime(2024, 1, 5, 10, 0, 0),
                    resolved_at=datetime(2024, 1, 6, 10, 0, 0),
                ),
            )
        )

        db.flush()

        for server_id, _, _, _ in servers:
            db.execute(
                text(
                    "UPDATE mcp_server_registry "
                    "SET meta = :meta, created_at = :created_at "
                    "WHERE server_id = :server_id"
                ),
                {
                    "meta": json.dumps({"contract_test": True}),
                    "created_at": now,
                    "server_id": server_id,
                },
            )

        db.commit()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/servers/srv1/detail")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "srv1", body
    assert body["name"] == "Alpha Server", body
    assert {axis["axis_name"] for axis in body["axes"]} == set(axis_names), body
    assert body["open_disputes"] == 2, body

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