from __future__ import annotations

from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}

router = APIRouter(prefix="/api", tags=["axis-scores"])


def get_p_critical_extremes(
    session: Session,
    axis: str | None = None,
    limit: int = 100,
) -> dict:
    if axis is not None and axis not in VALID_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"axis must be one of: {', '.join(sorted(VALID_AXES))}",
        )

    limit = min(limit, 500)
    params: dict[str, object] = {"limit": limit}
    axis_clause = ""

    if axis is not None:
        axis_clause = " AND s.axis_name = :axis"
        params["axis"] = axis

    if axis is None:
        count_sql = text(
            """
            SELECT COALESCE(SUM(group_count), 0) AS total_scored
            FROM (
                SELECT s.axis_name, COUNT(*) AS group_count
                FROM mcp_llm_axis_scores s
                JOIN mcp_server_registry r ON r.server_id = s.server_id
                WHERE s.p_critical IS NOT NULL
                GROUP BY s.axis_name
            ) grouped_scores
            """
        )
    else:
        count_sql = text(
            f"""
            SELECT COUNT(*) AS total_scored
            FROM mcp_llm_axis_scores s
            JOIN mcp_server_registry r ON r.server_id = s.server_id
            WHERE s.p_critical IS NOT NULL{axis_clause}
            """
        )

    total_scored = int(session.execute(count_sql, params).scalar_one())

    rows = session.execute(
        text(
            f"""
            SELECT
                s.server_id,
                r.name,
                s.p_critical,
                s.p_danger,
                s.p_top,
                s.escalated,
                s.escalated_to,
                s.label,
                s.scored_at
            FROM mcp_llm_axis_scores s
            JOIN mcp_server_registry r ON r.server_id = s.server_id
            WHERE s.p_critical IS NOT NULL{axis_clause}
            ORDER BY s.p_critical DESC, s.scored_at DESC, s.id ASC
            LIMIT :limit
            """
        ),
        params,
    ).all()

    extremes = []
    for row in rows:
        escalated = row.escalated
        if escalated is not None:
            escalated = bool(escalated)
        extremes.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "p_critical": row.p_critical,
                "p_danger": row.p_danger,
                "p_top": row.p_top,
                "escalated": escalated,
                "escalated_to": row.escalated_to,
                "label": row.label,
                "scored_at": row.scored_at,
            }
        )

    return {
        "axis": axis,
        "total_scored": total_scored,
        "extremes": extremes,
    }


@router.get("/axes/p_critical/extremes")
def p_critical_extremes(
    session: Session = Depends(get_session),
    axis: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
) -> dict:
    return get_p_critical_extremes(session=session, axis=axis, limit=limit)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    score_table = McpLlmAxisScore.__tablename__
    registry_table = McpServerRegistry.__tablename__

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {registry_table} (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description TEXT,
                    url TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    confidence REAL,
                    trust_score REAL,
                    scan_count INTEGER,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    last_assessed DATETIME,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {score_table} (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
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
                f"""
                INSERT INTO {registry_table} (server_id, name)
                VALUES
                    ('server-1', 'Critical Server'),
                    ('server-2', 'Moderate Server')
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {score_table} (
                    id, server_id, axis_name, label, label_index, probs,
                    p_top, p_critical, p_danger, escalated, escalated_to,
                    decision_rule_version, model_version, adapter_sha256, scored_at
                )
                VALUES
                    (
                        1, 'server-1', 'overall_risk', 'critical', 0, '{{}}',
                        0.95, 0.9, 0.8, 1, 'security',
                        'v1', 'model-v1', 'abc123',
                        '2025-01-01T00:00:00'
                    ),
                    (
                        2, 'server-2', 'overall_risk', 'moderate', 1, '{{}}',
                        0.5, 0.4, 0.3, 0, NULL,
                        'v1', 'model-v1', 'abc123',
                        '2025-01-02T00:00:00'
                    ),
                    (
                        3, 'server-1', 'overall_risk', 'unknown', 2, '{{}}',
                        NULL, NULL, NULL, 0, NULL,
                        'v1', 'model-v1', 'abc123',
                        '2025-01-03T00:00:00'
                    )
                """
            )
        )

    def override_get_session() -> Iterator[Session]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/axes/p_critical/extremes",
                params={"axis": "overall_risk", "limit": 10},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["axis"] == "overall_risk"
            assert payload["total_scored"] == 2
            assert len(payload["extremes"]) == 2
            assert payload["extremes"][0]["p_critical"] == 0.9
            assert all(item["p_critical"] is not None for item in payload["extremes"])
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()