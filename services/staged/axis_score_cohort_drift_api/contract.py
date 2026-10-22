from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import case, create_engine, distinct, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class CohortDriftRow(BaseModel):
    axis_name: str
    model_version: str
    decision_rule_version: str
    rows: int
    servers: int
    avg_p_top: float
    avg_p_critical: float
    critical_server_share: float


class CohortDriftResponse(BaseModel):
    generated_at: datetime
    cohorts: List[CohortDriftRow]


def query_cohort_drift(session: Session) -> List[CohortDriftRow]:
    critical_or_high = McpServerRegistry.risk_tier.in_(("CRITICAL", "HIGH"))
    critical_server = case(
        (critical_or_high, McpServerRegistry.server_id),
        else_=None,
    )

    statement = (
        select(
            McpLlmAxisScore.axis_name.label("axis_name"),
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.count(distinct(critical_server)).label("critical_servers"),
        )
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
    )

    cohorts = []
    for row in session.execute(statement):
        servers = int(row.servers or 0)
        critical_servers = int(row.critical_servers or 0)
        cohorts.append(
            CohortDriftRow(
                axis_name=row.axis_name,
                model_version=row.model_version,
                decision_rule_version=row.decision_rule_version,
                rows=int(row.rows or 0),
                servers=servers,
                avg_p_top=float(row.avg_p_top or 0.0),
                avg_p_critical=float(row.avg_p_critical or 0.0),
                critical_server_share=(
                    float(critical_servers) / float(servers) if servers else 0.0
                ),
            )
        )
    return cohorts


def get_cohort_drift(
    session: Session = Depends(get_session),
) -> CohortDriftResponse:
    return CohortDriftResponse(
        generated_at=datetime.now(timezone.utc),
        cohorts=query_cohort_drift(session),
    )


def create_app() -> FastAPI:
    application = FastAPI(title="axis_score_cohort_drift_api")
    application.get(
        "/api/scores/cohort-drift",
        response_model=CohortDriftResponse,
    )(get_cohort_drift)
    return application


app = create_app()


def _create_test_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    p_top FLOAT,
                    p_critical FLOAT,
                    model_version VARCHAR,
                    decision_rule_version VARCHAR,
                    scored_at DATETIME,
                    escalated BOOLEAN
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier)
                VALUES (:server_id, :risk_tier)
                """
            ),
            [
                {"server_id": "server-1", "risk_tier": "CRITICAL"},
                {"server_id": "server-2", "risk_tier": "HIGH"},
                {"server_id": "server-3", "risk_tier": "LOW"},
            ],
        )

        rows = []
        row_id = 1
        model_settings = {
            "model-v1": (
                {"server-1": 0.80, "server-2": 0.60, "server-3": 0.40},
                {"server-1": 0.20, "server-2": 0.15, "server-3": 0.10},
            ),
            "model-v2": (
                {"server-1": 0.90, "server-2": 0.70, "server-3": 0.50},
                {"server-1": 0.30, "server-2": 0.25, "server-3": 0.20},
            ),
        }
        for model_version, (top_values, critical_values) in model_settings.items():
            for axis_index in range(7):
                axis_name = f"axis-{axis_index}"
                for server_id in ("server-1", "server-2", "server-3"):
                    rows.append(
                        {
                            "id": row_id,
                            "server_id": server_id,
                            "axis_name": axis_name,
                            "p_top": top_values[server_id] + axis_index * 0.01,
                            "p_critical": critical_values[server_id],
                            "model_version": model_version,
                            "decision_rule_version": "rule-v1",
                            "scored_at": "2025-01-01T00:00:00+00:00",
                            "escalated": False,
                        }
                    )
                    row_id += 1

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id,
                    server_id,
                    axis_name,
                    p_top,
                    p_critical,
                    model_version,
                    decision_rule_version,
                    scored_at,
                    escalated
                ) VALUES (
                    :id,
                    :server_id,
                    :axis_name,
                    :p_top,
                    :p_critical,
                    :model_version,
                    :decision_rule_version,
                    :scored_at,
                    :escalated
                )
                """
            ),
            rows,
        )

    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def main() -> None:
    engine, session_factory = _create_test_store()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.get(
        "/api/scores/cohort-drift",
        response_model=CohortDriftResponse,
    )(get_cohort_drift)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scores/cohort-drift")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload.get("cohorts"), list)
    assert len(payload["cohorts"]) >= 2

    cohorts = {
        (
            cohort["axis_name"],
            cohort["model_version"],
            cohort["decision_rule_version"],
        ): cohort
        for cohort in payload["cohorts"]
    }
    expected_v1 = cohorts[("axis-0", "model-v1", "rule-v1")]
    expected_v2 = cohorts[("axis-0", "model-v2", "rule-v1")]

    assert expected_v1["rows"] == 3
    assert expected_v1["servers"] == 3
    assert abs(expected_v1["avg_p_top"] - 0.60) < 1e-9
    assert abs(expected_v1["avg_p_critical"] - 0.15) < 1e-9
    assert abs(expected_v1["critical_server_share"] - (2.0 / 3.0)) < 1e-9

    assert expected_v2["rows"] == 3
    assert expected_v2["servers"] == 3
    assert abs(expected_v2["avg_p_top"] - 0.70) < 1e-9
    assert abs(expected_v2["avg_p_critical"] - 0.25) < 1e-9
    assert abs(expected_v2["critical_server_share"] - (2.0 / 3.0)) < 1e-9

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()