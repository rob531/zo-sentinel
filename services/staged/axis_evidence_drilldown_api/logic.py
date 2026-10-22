from __future__ import annotations

from collections import OrderedDict
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _row_values(row: Any) -> dict[str, Any]:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _distinct(values: list[Any]) -> list[Any]:
    return list(OrderedDict((value, None) for value in values if value is not None).keys())


def group_axis_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group axis-score rows by axis and label, newest row first."""
    groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()

    for row in rows:
        key = (str(row.get("axis_name") or ""), str(row.get("label") or ""))
        groups.setdefault(key, []).append(row)

    result: list[dict[str, Any]] = []
    for (axis_name, label), group_rows in groups.items():
        latest = group_rows[0]
        history = [
            {
                "scored_at": row.get("scored_at"),
                "p_top": row.get("p_top"),
                "p_critical": row.get("p_critical"),
                "p_danger": row.get("p_danger"),
            }
            for row in group_rows
        ]

        escalated_rows = [row for row in group_rows if bool(row.get("escalated"))]
        escalated_row = escalated_rows[0] if escalated_rows else latest

        result.append(
            {
                "axis_name": axis_name,
                "label": label,
                "latest": {
                    "axis_name": latest.get("axis_name"),
                    "label": latest.get("label"),
                    "label_index": latest.get("label_index"),
                    "p_top": latest.get("p_top"),
                    "p_critical": latest.get("p_critical"),
                    "p_danger": latest.get("p_danger"),
                    "escalated": bool(latest.get("escalated")),
                    "escalated_to": latest.get("escalated_to"),
                    "decision_rule_version": latest.get("decision_rule_version"),
                    "model_version": latest.get("model_version"),
                    "scored_at": latest.get("scored_at"),
                },
                "history": history,
                "history_count": len(history),
                "versions": {
                    "decision_rule_version": _distinct(
                        [row.get("decision_rule_version") for row in group_rows]
                    ),
                    "model_version": _distinct(
                        [row.get("model_version") for row in group_rows]
                    ),
                },
                "escalated": bool(escalated_rows),
                "escalated_to": escalated_row.get("escalated_to"),
            }
        )

    return result


def fetch_axis_evidence(
    session: Session,
    server_id: str,
    axis_name: str,
) -> list[dict[str, Any]]:
    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.scored_at,
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
        )
        .order_by(desc(McpLlmAxisScore.scored_at))
        .limit(100)
    )

    result = session.execute(statement)
    return [_row_values(row) for row in result]


def get_axis_evidence(
    session: Session,
    server_id: str,
    axis_name: str,
) -> dict[str, Any] | None:
    rows = fetch_axis_evidence(session, server_id, axis_name)
    if not rows:
        return None

    groups = group_axis_evidence(rows)
    if not groups:
        return None

    evidence = groups[0]
    all_versions = {
        "decision_rule_version": _distinct(
            [row.get("decision_rule_version") for row in rows]
        ),
        "model_version": _distinct([row.get("model_version") for row in rows]),
    }
    evidence["versions"] = all_versions

    escalated_rows = [row for row in rows if bool(row.get("escalated"))]
    if escalated_rows:
        evidence["escalated"] = True
        evidence["escalated_to"] = escalated_rows[0].get("escalated_to")

    return evidence


def get_axis_evidence_for_server(
    session: Session,
    server_id: str,
    axis_name: str,
) -> dict[str, Any] | None:
    return get_axis_evidence(session, server_id, axis_name)


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    label_index INTEGER,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top, p_critical,
                     p_danger, escalated, escalated_to, decision_rule_version,
                     model_version, scored_at)
                VALUES
                    ('server-1', 'overall_risk', 'high', 2, 0.90, 0.70, 0.80,
                     1, 'security', 'rules-v1', 'model-a', '2024-01-03 00:00:00'),
                    ('server-1', 'overall_risk', 'high', 2, 0.80, 0.60, 0.70,
                     0, NULL, 'rules-v1', 'model-b', '2024-01-02 00:00:00'),
                    ('server-1', 'overall_risk', 'high', 2, 0.70, 0.50, 0.60,
                     0, NULL, 'rules-v1', 'model-b', '2024-01-01 00:00:00')
                """
            )
        )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/servers/{server_id}/axes/evidence")
    def read_axis_evidence(
        server_id: str,
        axis_name: str,
        session: Session = Depends(get_session),
    ):
        evidence = get_axis_evidence(session, server_id, axis_name)
        if evidence is None:
            return {"server_id": server_id, "axis_name": axis_name, "history": []}
        return {
            "server_id": server_id,
            "axis_name": evidence["axis_name"],
            "label": evidence["label"],
            "latest": evidence["latest"],
            "history_count": evidence["history_count"],
            "history": evidence["history"],
            "versions": evidence["versions"],
            "escalated": evidence["escalated"],
            "escalated_to": evidence["escalated_to"],
        }

    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    with TestClient(that_app) as client:
        response = client.get(
            "/api/servers/server-1/axes/evidence",
            params={"axis_name": "overall_risk"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["history_count"] == 3
        assert len(payload["versions"]["model_version"]) == 2
        assert payload["escalated"] is True

    print("PASS")