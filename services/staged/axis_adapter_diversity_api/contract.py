from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class AdapterDiversity(BaseModel):
    adapter_sha256: str
    rows: int
    servers: int
    model_versions: int
    axes: int
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None


class AdapterDiversityResponse(BaseModel):
    generated_at: datetime
    adapters: list[AdapterDiversity]
    adapter_sha256_missing: int
    distinct_adapters: int


router = APIRouter(prefix="/api")


@router.get(
    "/axes/adapter-diversity",
    response_model=AdapterDiversityResponse,
)
def adapter_diversity(
    session: Session = Depends(get_session),
) -> AdapterDiversityResponse:
    aggregate_sql = text(
        """
        SELECT
            adapter_sha256,
            COUNT(*) AS rows,
            COUNT(DISTINCT server_id) AS servers,
            COUNT(DISTINCT model_version) AS model_versions,
            COUNT(DISTINCT axis_name) AS axes,
            MIN(scored_at) AS first_scored_at,
            MAX(scored_at) AS last_scored_at
        FROM mcp_llm_axis_scores
        WHERE adapter_sha256 IS NOT NULL
          AND adapter_sha256 <> ''
        GROUP BY adapter_sha256
        ORDER BY adapter_sha256
        """
    )
    missing_sql = text(
        """
        SELECT COUNT(*)
        FROM mcp_llm_axis_scores
        WHERE adapter_sha256 IS NULL OR adapter_sha256 = ''
        """
    )
    distinct_sql = text(
        """
        SELECT COUNT(DISTINCT adapter_sha256)
        FROM mcp_llm_axis_scores
        WHERE adapter_sha256 IS NOT NULL
          AND adapter_sha256 <> ''
        """
    )

    rows = session.execute(aggregate_sql).mappings().all()
    missing = int(session.execute(missing_sql).scalar_one())
    distinct = int(session.execute(distinct_sql).scalar_one())

    return AdapterDiversityResponse(
        generated_at=datetime.now(timezone.utc),
        adapters=[
            AdapterDiversity(
                adapter_sha256=str(row["adapter_sha256"]),
                rows=int(row["rows"]),
                servers=int(row["servers"]),
                model_versions=int(row["model_versions"]),
                axes=int(row["axes"]),
                first_scored_at=row["first_scored_at"],
                last_scored_at=row["last_scored_at"],
            )
            for row in rows
        ],
        adapter_sha256_missing=missing,
        distinct_adapters=distinct,
    )


def ensure_tables(engine) -> None:
    table_name = McpLlmAxisScore.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    adapter_sha256 TEXT,
                    scored_at TEXT
                )
                """
            )
        )


def seed_data(engine) -> None:
    rows = [
        {
            "server_id": "server-1",
            "axis_name": "axis-a",
            "label": "a",
            "label_index": 0,
            "probs": '{"a": 0.8, "b": 0.2}',
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.2,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-1",
            "adapter_sha256": "adapter-a",
            "scored_at": "2025-01-01T00:00:00",
        },
        {
            "server_id": "server-2",
            "axis_name": "axis-b",
            "label": "b",
            "label_index": 1,
            "probs": '{"a": 0.2, "b": 0.8}',
            "p_top": 0.8,
            "p_critical": 0.2,
            "p_danger": 0.1,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-1",
            "adapter_sha256": "adapter-a",
            "scored_at": "2025-01-02T00:00:00",
        },
        {
            "server_id": "server-3",
            "axis_name": "axis-c",
            "label": "c",
            "label_index": 2,
            "probs": '{"a": 0.1, "b": 0.9}',
            "p_top": 0.9,
            "p_critical": 0.3,
            "p_danger": 0.4,
            "escalated": 1,
            "escalated_to": "server-3",
            "decision_rule_version": "rule-v1",
            "model_version": "model-2",
            "adapter_sha256": "adapter-a",
            "scored_at": "2025-01-03T00:00:00",
        },
        {
            "server_id": "server-1",
            "axis_name": "axis-a",
            "label": "a",
            "label_index": 0,
            "probs": '{"a": 0.8, "b": 0.2}',
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.2,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-1",
            "adapter_sha256": "adapter-b",
            "scored_at": "2025-01-04T00:00:00",
        },
        {
            "server_id": "server-2",
            "axis_name": "axis-b",
            "label": "b",
            "label_index": 1,
            "probs": '{"a": 0.2, "b": 0.8}',
            "p_top": 0.8,
            "p_critical": 0.2,
            "p_danger": 0.1,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-1",
            "adapter_sha256": "adapter-b",
            "scored_at": "2025-01-05T00:00:00",
        },
        {
            "server_id": "server-3",
            "axis_name": "axis-c",
            "label": "c",
            "label_index": 2,
            "probs": '{"a": 0.1, "b": 0.9}',
            "p_top": 0.9,
            "p_critical": 0.3,
            "p_danger": 0.4,
            "escalated": 1,
            "escalated_to": "server-3",
            "decision_rule_version": "rule-v1",
            "model_version": "model-2",
            "adapter_sha256": "adapter-b",
            "scored_at": "2025-01-06T00:00:00",
        },
        {
            "server_id": "server-4",
            "axis_name": "axis-a",
            "label": "a",
            "label_index": 0,
            "probs": '{"a": 0.8, "b": 0.2}',
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.2,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-3",
            "adapter_sha256": "adapter-c",
            "scored_at": "2025-01-07T00:00:00",
        },
        {
            "server_id": "server-4",
            "axis_name": "axis-b",
            "label": "b",
            "label_index": 1,
            "probs": '{"a": 0.2, "b": 0.8}',
            "p_top": 0.8,
            "p_critical": 0.2,
            "p_danger": 0.1,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-3",
            "adapter_sha256": "adapter-c",
            "scored_at": "2025-01-08T00:00:00",
        },
        {
            "server_id": "server-5",
            "axis_name": "axis-c",
            "label": "c",
            "label_index": 2,
            "probs": '{"a": 0.1, "b": 0.9}',
            "p_top": 0.9,
            "p_critical": 0.3,
            "p_danger": 0.4,
            "escalated": 1,
            "escalated_to": "server-5",
            "decision_rule_version": "rule-v1",
            "model_version": "model-4",
            "adapter_sha256": "adapter-c",
            "scored_at": "2025-01-09T00:00:00",
        },
        {
            "server_id": "server-6",
            "axis_name": "axis-a",
            "label": "a",
            "label_index": 0,
            "probs": '{"a": 0.8, "b": 0.2}',
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.2,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "rule-v1",
            "model_version": "model-4",
            "adapter_sha256": None,
            "scored_at": "2025-01-10T00:00:00",
        },
    ]

    insert_sql = text(
        """
        INSERT INTO mcp_llm_axis_scores (
            server_id, axis_name, label, label_index, probs,
            p_top, p_critical, p_danger, escalated, escalated_to,
            decision_rule_version, model_version, adapter_sha256, scored_at
        ) VALUES (
            :server_id, :axis_name, :label, :label_index, :probs,
            :p_top, :p_critical, :p_danger, :escalated, :escalated_to,
            :decision_rule_version, :model_version, :adapter_sha256, :scored_at
        )
        """
    )
    with engine.begin() as connection:
        for row in rows:
            connection.execute(insert_sql, row)


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    ensure_tables(engine)
    seed_data(engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/adapter-diversity")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["distinct_adapters"] == 3, payload
        assert payload["adapter_sha256_missing"] == 1, payload
        assert len(payload["adapters"]) == 3, payload

    print("PASS")


if __name__ == "__main__":
    main()