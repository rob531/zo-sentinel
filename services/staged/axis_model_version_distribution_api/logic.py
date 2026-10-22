from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_axis_model_version_distribution(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    group_stmt = (
        select(
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            func.max(McpLlmAxisScore.adapter_sha256).label("adapter_sha256"),
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .order_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
    )

    group_rows = session.execute(group_stmt).all()
    groups = [
        {
            "model_version": row.model_version,
            "decision_rule_version": row.decision_rule_version,
            "adapter_sha256": row.adapter_sha256,
            "rows": int(row.rows or 0),
            "servers": int(row.servers or 0),
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top is not None else 0.0,
            "last_scored_at": row.last_scored_at,
        }
        for row in group_rows
    ]

    by_axis_stmt = (
        select(
            McpLlmAxisScore.axis_name.label("axis_name"),
            McpLlmAxisScore.model_version.label("model_version"),
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
        )
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
        )
    )

    axis_rows = session.execute(by_axis_stmt).all()
    by_axis = [
        {
            "axis_name": row.axis_name,
            "model_version": row.model_version,
            "rows": int(row.rows or 0),
            "servers": int(row.servers or 0),
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top is not None else 0.0,
        }
        for row in axis_rows
    ]

    present_axes = {row["axis_name"] for row in by_axis}
    fallback_model_version = groups[0]["model_version"] if groups else None
    for axis_name in AXIS_NAMES:
        if axis_name not in present_axes:
            by_axis.append(
                {
                    "axis_name": axis_name,
                    "model_version": fallback_model_version,
                    "rows": 0,
                    "servers": 0,
                    "avg_p_top": 0.0,
                }
            )

    axis_order = {axis_name: index for index, axis_name in enumerate(AXIS_NAMES)}
    by_axis.sort(
        key=lambda row: (
            axis_order.get(row["axis_name"], len(AXIS_NAMES)),
            row["model_version"] or "",
        )
    )

    return {
        "total_scored": sum(row["rows"] for row in groups),
        "groups": groups,
        "by_axis": by_axis,
    }


get_model_version_distribution = get_axis_model_version_distribution
get_distribution = get_axis_model_version_distribution
get_axis_model_version_distribution_logic = get_axis_model_version_distribution
get_model_version_distribution_logic = get_axis_model_version_distribution


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    axis_name VARCHAR NOT NULL,
                    model_version VARCHAR NOT NULL,
                    decision_rule_version VARCHAR NOT NULL,
                    adapter_sha256 VARCHAR,
                    scored_at DATETIME NOT NULL,
                    server_id VARCHAR NOT NULL,
                    p_top FLOAT
                )
                """
            )
        )

        rows = []
        for server_number in range(3):
            model_version = "model-v1" if server_number != 1 else "model-v2"
            for axis_index, axis_name in enumerate(AXIS_NAMES):
                rows.append(
                    {
                        "axis_name": axis_name,
                        "model_version": model_version,
                        "decision_rule_version": "rules-v1",
                        "adapter_sha256": "adapter-sha",
                        "scored_at": datetime(2025, 1, 1 + axis_index, 12, 0, 0),
                        "server_id": f"server-{server_number + 1}",
                        "p_top": 0.25 + axis_index / 100.0,
                    }
                )

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    axis_name,
                    model_version,
                    decision_rule_version,
                    adapter_sha256,
                    scored_at,
                    server_id,
                    p_top
                ) VALUES (
                    :axis_name,
                    :model_version,
                    :decision_rule_version,
                    :adapter_sha256,
                    :scored_at,
                    :server_id,
                    :p_top
                )
                """
            ),
            rows,
        )

    def override_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/axis/model-version-distribution")
    def distribution(
        result: dict[str, Any] = Depends(get_axis_model_version_distribution),
    ) -> dict[str, Any]:
        return result

    with SessionLocal() as session:
        result = get_axis_model_version_distribution(session)

    assert result["total_scored"] == 21
    assert len(result["groups"]) == 2
    model_v1 = next(row for row in result["groups"] if row["model_version"] == "model-v1")
    assert model_v1["rows"] == 14
    assert {row["axis_name"] for row in result["by_axis"]} == set(AXIS_NAMES)

    print("PASS")