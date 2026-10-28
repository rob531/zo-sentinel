from collections.abc import Iterator

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_escalation_target_rollup

router = APIRouter(prefix="/api")


@router.get("/axes/escalation-targets")
def axis_escalation_target_rollup(
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return get_escalation_target_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    valid_axes = {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    axis_name VARCHAR,
                    label VARCHAR,
                    label_index INTEGER,
                    probs VARCHAR,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    adapter_sha256 VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id, server_id, axis_name, label, label_index, probs,
                    p_top, p_critical, p_danger, escalated, escalated_to,
                    decision_rule_version, model_version, adapter_sha256, scored_at
                ) VALUES
                    (1, 10, 'overall_risk', 'critical', 3, '{}', 0.91, 0.91, 0.05, 1, 'CRITICAL', 'v1', 'm1', 'sha1', CURRENT_TIMESTAMP),
                    (2, 10, 'auth_strength', 'high', 2, '{}', 0.83, 0.20, 0.83, 1, 'HIGH', 'v1', 'm1', 'sha1', CURRENT_TIMESTAMP),
                    (3, 11, 'capability_breadth', 'critical', 3, '{}', 0.72, 0.72, 0.10, 1, 'CRITICAL', 'v1', 'm1', 'sha1', CURRENT_TIMESTAMP),
                    (4, 11, 'data_sensitivity', 'low', 0, '{}', 0.31, 0.05, 0.31, 0, NULL, 'v1', 'm1', 'sha1', CURRENT_TIMESTAMP),
                    (5, 12, 'network_egress', 'medium', 1, '{}', 0.44, 0.10, 0.44, 0, NULL, 'v1', 'm1', 'sha1', CURRENT_TIMESTAMP)
                """
            )
        )

    test_session = Session(engine)

    def override_get_session() -> Iterator[Session]:
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        response = TestClient(that_app).get("/api/axes/escalation-targets")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_escalated"] == 3
        assert sorted(payload["by_escalated_to"], key=lambda item: item["escalated_to"]) == [
            {"escalated_to": "CRITICAL", "count": 2},
            {"escalated_to": "HIGH", "count": 1},
        ]
        assert all(item["axis_name"] in valid_axes for item in payload["by_axis"])
        print("PASS")
    finally:
        test_session.close()
        engine.dispose()