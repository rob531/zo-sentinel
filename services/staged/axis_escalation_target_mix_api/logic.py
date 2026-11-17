from collections.abc import Iterator
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

KNOWN_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _escalated_rows() -> Any:
    return select(
        McpLlmAxisScore.escalated_to,
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.id,
    ).where(
        McpLlmAxisScore.escalated.is_(True),
        McpLlmAxisScore.axis_name.in_(KNOWN_AXES),
    )


def get_escalation_target_mix(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    target_counts = db.execute(
        select(
            McpLlmAxisScore.escalated_to,
            func.count(McpLlmAxisScore.id).label("count"),
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.axis_name.in_(KNOWN_AXES),
        )
        .group_by(McpLlmAxisScore.escalated_to)
    ).all()

    target_servers = db.execute(
        _escalated_rows().with_only_columns(
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.server_id,
        ).distinct()
    ).all()

    servers_by_target: dict[Any, list[Any]] = {}
    all_servers: set[Any] = set()
    for target, server_id in target_servers:
        if server_id is None:
            continue
        servers_by_target.setdefault(target, []).append(server_id)
        all_servers.add(server_id)

    escalated_to: dict[Any, dict[str, Any]] = {}
    for target, count in target_counts:
        servers = sorted(
            set(servers_by_target.get(target, [])),
            key=lambda value: (value is None, str(value)),
        )
        escalated_to[target] = {"count": int(count), "servers": servers}

    axis_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.max(McpLlmAxisScore.p_critical).label("max_p_critical"),
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.axis_name.in_(KNOWN_AXES),
        )
        .group_by(McpLlmAxisScore.axis_name)
    ).all()

    by_axis: dict[str, dict[str, Any]] = {}
    for axis_name, count, avg_p_top, max_p_critical in axis_rows:
        by_axis[axis_name] = {
            "count": int(count),
            "avg_p_top": float(avg_p_top) if avg_p_top is not None else None,
            "max_p_critical": (
                float(max_p_critical) if max_p_critical is not None else None
            ),
        }

    total_escalated = sum(item["count"] for item in escalated_to.values())

    return {
        "escalated_to": escalated_to,
        "by_axis": by_axis,
        "total_escalated": total_escalated,
        "servers_escalated": len(all_servers),
    }


get_axis_escalation_target_mix = get_escalation_target_mix
get_axis_escalation_targets = get_escalation_target_mix
get_escalation_targets = get_escalation_target_mix
get_target_mix = get_escalation_target_mix


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                axis_name VARCHAR NOT NULL,
                label VARCHAR,
                label_index INTEGER,
                probs TEXT,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated BOOLEAN NOT NULL,
                escalated_to VARCHAR,
                decision_rule_version VARCHAR,
                model_version VARCHAR,
                scored_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores (
                server_id, axis_name, label, label_index, probs,
                p_top, p_critical, p_danger, escalated, escalated_to,
                decision_rule_version, model_version, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    101,
                    "overall_risk",
                    "critical",
                    3,
                    '{"overall_risk": 0.91}',
                    0.91,
                    0.88,
                    0.73,
                    True,
                    "critical",
                    "v1",
                    "model-v1",
                    "2025-01-01T00:00:00",
                ),
                (
                    102,
                    "auth_strength",
                    "review",
                    1,
                    '{"auth_strength": 0.74}',
                    0.74,
                    0.63,
                    0.52,
                    True,
                    "review",
                    "v1",
                    "model-v1",
                    "2025-01-01T00:00:00",
                ),
                (
                    103,
                    "overall_risk",
                    "low",
                    0,
                    '{"overall_risk": 0.21}',
                    0.21,
                    0.08,
                    0.12,
                    False,
                    None,
                    "v1",
                    "model-v1",
                    "2025-01-01T00:00:00",
                ),
                (
                    104,
                    "auth_strength",
                    "low",
                    0,
                    '{"auth_strength": 0.18}',
                    0.18,
                    0.06,
                    0.10,
                    False,
                    None,
                    "v1",
                    "model-v1",
                    "2025-01-01T00:00:00",
                ),
            ],
        )

    def override_get_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/axes/escalation/targets")
    def read_escalation_targets(
        result: dict[str, Any] = Depends(get_escalation_target_mix),
    ) -> dict[str, Any]:
        return result

    with TestClient(that_app) as client:
        response = client.get("/api/axes/escalation/targets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_escalated"] == 2
        assert len(payload["escalated_to"]) == 2
        assert len(payload["by_axis"]) >= 1

    print("PASS")