from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base


class DecisionRuleVersionSummary(BaseModel):
    decision_rule_version: Optional[str]
    row_count: int
    server_count: int
    model_versions: int
    escalated_rows: int
    first_scored_at: Optional[datetime]
    last_scored_at: Optional[datetime]


class DecisionRuleVersionsResponse(BaseModel):
    days: int
    versions: List[DecisionRuleVersionSummary]


app = FastAPI(title="Axis Score Decision Rule Versions API")


def get_decision_rule_version_spread(
    session: Session,
    days: int,
) -> List[DecisionRuleVersionSummary]:
    cutoff = datetime.utcnow() - timedelta(days=days)

    result = session.execute(
        text(
            """
            SELECT
                decision_rule_version,
                COUNT(*) AS row_count,
                COUNT(DISTINCT server_id) AS server_count,
                COUNT(DISTINCT model_version) AS model_versions,
                COALESCE(
                    SUM(CASE WHEN escalated THEN 1 ELSE 0 END),
                    0
                ) AS escalated_rows,
                MIN(scored_at) AS first_scored_at,
                MAX(scored_at) AS last_scored_at
            FROM mcp_llm_axis_scores
            WHERE scored_at >= :cutoff
            GROUP BY decision_rule_version
            ORDER BY row_count DESC, decision_rule_version ASC
            """
        ),
        {"cutoff": cutoff},
    )

    return [
        DecisionRuleVersionSummary(
            decision_rule_version=row.decision_rule_version,
            row_count=int(row.row_count),
            server_count=int(row.server_count),
            model_versions=int(row.model_versions),
            escalated_rows=int(row.escalated_rows or 0),
            first_scored_at=row.first_scored_at,
            last_scored_at=row.last_scored_at,
        )
        for row in result
    ]


@app.get(
    "/api/axis-scores/decision-rule-versions",
    response_model=DecisionRuleVersionsResponse,
)
def decision_rule_versions(
    days: int = Query(30),
    session: Session = Depends(get_session),
) -> DecisionRuleVersionsResponse:
    versions = get_decision_rule_version_spread(session, days)
    return DecisionRuleVersionsResponse(days=days, versions=versions)


def _create_test_engine() -> Engine:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_test_data(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER,
                escalated_to TEXT,
                decision_rule_version TEXT,
                model_version TEXT,
                adapter_sha256 TEXT,
                scored_at DATETIME
            )
            """
        )
    )

    now = datetime.utcnow()
    rows = [
        {
            "id": 1,
            "server_id": "server-a",
            "axis_name": "availability",
            "label": "healthy",
            "label_index": 0,
            "p_top": 0.91,
            "p_critical": 0.04,
            "p_danger": 0.05,
            "escalated": 1,
            "escalated_to": "operator-a",
            "decision_rule_version": "v2",
            "model_version": "model-1",
            "adapter_sha256": "adapter-a",
            "scored_at": now - timedelta(days=1),
        },
        {
            "id": 2,
            "server_id": "server-b",
            "axis_name": "availability",
            "label": "degraded",
            "label_index": 1,
            "p_top": 0.62,
            "p_critical": 0.18,
            "p_danger": 0.20,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "v2",
            "model_version": "model-2",
            "adapter_sha256": "adapter-b",
            "scored_at": now - timedelta(days=2),
        },
        {
            "id": 3,
            "server_id": "server-a",
            "axis_name": "availability",
            "label": "degraded",
            "label_index": 1,
            "p_top": 0.58,
            "p_critical": 0.22,
            "p_danger": 0.20,
            "escalated": 1,
            "escalated_to": "operator-b",
            "decision_rule_version": "v2",
            "model_version": "model-2",
            "adapter_sha256": "adapter-c",
            "scored_at": now - timedelta(days=3),
        },
        {
            "id": 4,
            "server_id": "server-c",
            "axis_name": "availability",
            "label": "healthy",
            "label_index": 0,
            "p_top": 0.88,
            "p_critical": 0.05,
            "p_danger": 0.07,
            "escalated": 0,
            "escalated_to": None,
            "decision_rule_version": "v1",
            "model_version": "model-3",
            "adapter_sha256": "adapter-d",
            "scored_at": now - timedelta(days=4),
        },
    ]

    session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores (
                id,
                server_id,
                axis_name,
                label,
                label_index,
                p_top,
                p_critical,
                p_danger,
                escalated,
                escalated_to,
                decision_rule_version,
                model_version,
                adapter_sha256,
                scored_at
            ) VALUES (
                :id,
                :server_id,
                :axis_name,
                :label,
                :label_index,
                :p_top,
                :p_critical,
                :p_danger,
                :escalated,
                :escalated_to,
                :decision_rule_version,
                :model_version,
                :adapter_sha256,
                :scored_at
            )
            """
        ),
        rows,
    )
    session.commit()


def main() -> None:
    engine = _create_test_engine()
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    session = testing_session_local()
    try:
        _seed_test_data(session)
    finally:
        session.close()

    def override_get_session():
        override_session = testing_session_local()
        try:
            yield override_session
        finally:
            override_session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/axis-scores/decision-rule-versions",
                params={"days": 30},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 30
            versions = payload["versions"]
            assert len(versions) == 2
            assert versions[0]["decision_rule_version"] == "v2"
            assert versions[0]["row_count"] == 3
            assert versions[0]["server_count"] == 2
            assert versions[0]["model_versions"] == 2
            assert versions[0]["escalated_rows"] == 2
            assert versions[1]["decision_rule_version"] == "v1"
            assert versions[1]["row_count"] == 1
            assert versions[1]["server_count"] == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()