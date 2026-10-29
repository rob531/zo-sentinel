from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import compute_drift

router = APIRouter(prefix="/api")


@router.get("/scores/model-drift")
def get_model_drift(session: Session = Depends(get_session)) -> dict:
    statement = select(
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.adapter_sha256,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.scored_at,
    )
    result = session.execute(statement).mappings().all()
    return compute_drift([dict(row) for row in result])


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    model_version TEXT,
                    adapter_sha256 TEXT,
                    decision_rule_version TEXT,
                    server_id TEXT,
                    escalated BOOLEAN,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id,
                    model_version,
                    adapter_sha256,
                    decision_rule_version,
                    server_id,
                    escalated,
                    scored_at
                ) VALUES (
                    :id,
                    :model_version,
                    :adapter_sha256,
                    :decision_rule_version,
                    :server_id,
                    :escalated,
                    :scored_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "model_version": "sft-v3",
                    "adapter_sha256": "a" * 64,
                    "decision_rule_version": "r1",
                    "server_id": "1",
                    "escalated": 1,
                    "scored_at": "2024-01-01 00:00:00",
                },
                {
                    "id": 2,
                    "model_version": "sft-v3",
                    "adapter_sha256": "b" * 64,
                    "decision_rule_version": "r1",
                    "server_id": "2",
                    "escalated": 0,
                    "scored_at": "2024-01-02 00:00:00",
                },
                {
                    "id": 3,
                    "model_version": None,
                    "adapter_sha256": "c" * 64,
                    "decision_rule_version": "r2",
                    "server_id": "3",
                    "escalated": 0,
                    "scored_at": "2024-01-03 00:00:00",
                },
            ],
        )

    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/scores/model-drift")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["distinct_model_versions"] == 2
        assert any(
            bucket["model_version"] == "unknown" and bucket["rows"] == 1
            for bucket in payload["model_versions"]
        )
        assert any(
            bucket["decision_rule_version"] == "r2"
            for bucket in payload["rule_versions"]
        )

    print("PASS")