from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _isoformat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_axes(session: Session) -> dict[str, Any]:
    total_rows = int(
        session.execute(
            select(func.count()).select_from(McpLlmAxisScore)
        ).scalar()
        or 0
    )

    null_version_rows = int(
        session.execute(
            select(func.count())
            .select_from(McpLlmAxisScore)
            .where(McpLlmAxisScore.decision_rule_version.is_(None))
        ).scalar()
        or 0
    )

    decision_rows = session.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.count(func.distinct(McpLlmAxisScore.adapter_sha256)).label(
                "distinct_adapters"
            ),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
            func.min(McpLlmAxisScore.scored_at).label("min_scored_at"),
        )
        .where(McpLlmAxisScore.decision_rule_version.is_not(None))
        .group_by(McpLlmAxisScore.decision_rule_version)
        .order_by(McpLlmAxisScore.decision_rule_version)
    ).all()

    model_rows = session.execute(
        select(
            McpLlmAxisScore.model_version,
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
        )
        .group_by(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.model_version)
    ).all()

    return {
        "total_rows": total_rows,
        "by_decision_rule_version": [
            {
                "decision_rule_version": str(row.decision_rule_version),
                "rows": int(row.rows),
                "servers": int(row.servers),
                "distinct_adapters": int(row.distinct_adapters),
                "latest_scored_at": _isoformat(row.latest_scored_at),
                "min_scored_at": _isoformat(row.min_scored_at),
            }
            for row in decision_rows
        ],
        "by_model_version": [
            {
                "model_version": (
                    "" if row.model_version is None else str(row.model_version)
                ),
                "rows": int(row.rows),
                "servers": int(row.servers),
            }
            for row in model_rows
        ],
        "null_version_rows": null_version_rows,
    }


def get_decision_rule_version_distribution(session: Session) -> dict[str, Any]:
    return get_axes(session)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    adapter_sha256 VARCHAR,
                    axis_name VARCHAR,
                    scored_at DATETIME,
                    server_id VARCHAR
                )
                """
            )
        )

        seeded_rows = 0
        for version_index, decision_rule_version in enumerate(
            ("decision-v1", "decision-v2", "decision-v3"), start=1
        ):
            for axis_index, axis_name in enumerate(("accuracy", "safety"), start=1):
                connection.execute(
                    text(
                        """
                        INSERT INTO mcp_llm_axis_scores (
                            decision_rule_version,
                            model_version,
                            adapter_sha256,
                            axis_name,
                            scored_at,
                            server_id
                        ) VALUES (
                            :decision_rule_version,
                            :model_version,
                            :adapter_sha256,
                            :axis_name,
                            :scored_at,
                            :server_id
                        )
                        """
                    ),
                    {
                        "decision_rule_version": decision_rule_version,
                        "model_version": f"model-v{axis_index}",
                        "adapter_sha256": f"adapter-{version_index}-{axis_index}",
                        "axis_name": axis_name,
                        "scored_at": (
                            f"2024-01-{version_index:02d}T"
                            f"{axis_index:02d}:00:00"
                        ),
                        "server_id": f"server-{axis_index}",
                    },
                )
                seeded_rows += 1

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    decision_rule_version,
                    model_version,
                    adapter_sha256,
                    axis_name,
                    scored_at,
                    server_id
                ) VALUES (
                    NULL,
                    'model-v1',
                    'adapter-null',
                    'accuracy',
                    '2024-01-04T01:00:00',
                    'server-3'
                )
                """
            )
        )
        seeded_rows += 1

    local_app = FastAPI()

    @local_app.get("/api/axis/decision-rule-versions")
    def decision_rule_versions(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_axes(session)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/axis/decision-rule-versions")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_rows"] == seeded_rows
        assert payload["null_version_rows"] == 1
        assert len(payload["by_decision_rule_version"]) == 3
        assert {
            item["decision_rule_version"]
            for item in payload["by_decision_rule_version"]
        } == {"decision-v1", "decision-v2", "decision-v3"}

    print("PASS")