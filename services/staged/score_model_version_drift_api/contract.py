from datetime import datetime, timedelta, timezone
from typing import Iterator, List

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class ModelVersionCoverage(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    row_count: int
    server_count: int
    first_scored_at: datetime
    last_scored_at: datetime


class ModelVersionsResponse(BaseModel):
    versions: List[ModelVersionCoverage]
    window_days: int
    total_versions: int


def build_service() -> FastAPI:
    app = FastAPI(title="Model Version Drift API")

    @app.get(
        "/api/scores/model-versions",
        response_model=ModelVersionsResponse,
    )
    def list_model_versions(
        days: int = Query(default=30, ge=0),
        session: Session = Depends(get_session),
    ) -> ModelVersionsResponse:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        bind = session.get_bind()
        if bind.dialect.name == "sqlite":
            cutoff_value = cutoff.replace(tzinfo=None)
        else:
            cutoff_value = cutoff

        statement = text(
            """
            SELECT
                COALESCE(model_version, 'unknown') AS model_version,
                COALESCE(decision_rule_version, 'unknown') AS decision_rule_version,
                COALESCE(adapter_sha256, 'unknown') AS adapter_sha256,
                CAST(COUNT(*) AS INTEGER) AS row_count,
                CAST(COUNT(DISTINCT server_id) AS INTEGER) AS server_count,
                MIN(scored_at) AS first_scored_at,
                MAX(scored_at) AS last_scored_at
            FROM mcp_llm_axis_scores
            WHERE scored_at >= :cutoff
            GROUP BY
                COALESCE(model_version, 'unknown'),
                COALESCE(decision_rule_version, 'unknown'),
                COALESCE(adapter_sha256, 'unknown')
            ORDER BY MAX(scored_at) DESC
            """
        )

        rows = session.execute(statement, {"cutoff": cutoff_value}).mappings().all()
        versions = [ModelVersionCoverage(**dict(row)) for row in rows]

        return ModelVersionsResponse(
            versions=versions,
            window_days=days,
            total_versions=len(versions),
        )

    return app


app = build_service()


def _run_contract() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    model_version TEXT,
                    decision_rule_version TEXT,
                    adapter_sha256 TEXT,
                    axis_name TEXT,
                    scored_at DATETIME NOT NULL,
                    server_id TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded_rows = [
        {
            "model_version": "sft-model-v2",
            "decision_rule_version": "rules-v2",
            "adapter_sha256": "adapter-v2",
            "axis_name": "axis_a",
            "scored_at": now,
            "server_id": "server-1",
        },
        {
            "model_version": "sft-model-v2",
            "decision_rule_version": "rules-v2",
            "adapter_sha256": "adapter-v2",
            "axis_name": "axis_b",
            "scored_at": now - timedelta(hours=1),
            "server_id": "server-2",
        },
        {
            "model_version": "sft-model-v1",
            "decision_rule_version": "rules-v1",
            "adapter_sha256": "adapter-v1",
            "axis_name": "axis_a",
            "scored_at": now - timedelta(hours=2),
            "server_id": "server-1",
        },
    ]

    session = testing_session()
    try:
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    model_version,
                    decision_rule_version,
                    adapter_sha256,
                    axis_name,
                    scored_at,
                    server_id
                ) VALUES (
                    :model_version,
                    :decision_rule_version,
                    :adapter_sha256,
                    :axis_name,
                    :scored_at,
                    :server_id
                )
                """
            ),
            seeded_rows,
        )
        session.commit()
    finally:
        session.close()

    def override_get_session() -> Iterator[Session]:
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    contract_app = build_service()
    contract_app.dependency_overrides[get_session] = override_get_session

    with TestClient(contract_app) as client:
        response = client.get("/api/scores/model-versions")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["window_days"] == 30, payload
    assert payload["total_versions"] == 2, payload
    assert len(payload["versions"]) == 2, payload

    newest = payload["versions"][0]
    assert newest["model_version"] == "sft-model-v2", payload
    assert newest["row_count"] == 2, payload
    assert newest["server_count"] == 2, payload
    assert newest["first_scored_at"] is not None, payload
    assert newest["last_scored_at"] is not None, payload

    print("PASS")


if __name__ == "__main__":
    _run_contract()