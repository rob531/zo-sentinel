from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_scoring_coverage_gaps as _get_scoring_coverage_gaps
except ImportError:
    try:
        from .logic import get_coverage_gaps as _get_scoring_coverage_gaps
    except ImportError:
        try:
            from .logic import get_axis_score_coverage_gaps as _get_scoring_coverage_gaps
        except ImportError:
            from .logic import get_axis_coverage_gaps as _get_scoring_coverage_gaps

router = APIRouter(prefix="/api")


@router.get("/scoring/coverage-gaps")
async def scoring_coverage_gaps(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = _get_scoring_coverage_gaps(session)
    if isawaitable(result):
        result = await result
    return result


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

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
                    server_id TEXT PRIMARY KEY,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    p_top REAL,
                    scored_at TEXT,
                    model_version TEXT,
                    decision_rule_version TEXT
                )
                """
            )
        )

    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]

    with engine.begin() as connection:
        for server_id in ("server-1", "server-2", "server-3"):
            connection.execute(
                text(
                    "INSERT INTO mcp_server_registry "
                    "(server_id, risk_tier) VALUES (:server_id, :risk_tier)"
                ),
                {"server_id": server_id, "risk_tier": "medium"},
            )

        rows = []
        for axis_name in axes:
            rows.append(
                {
                    "server_id": "server-1",
                    "axis_name": axis_name,
                    "p_top": 0.5,
                    "scored_at": "2025-01-01T00:00:00+00:00",
                    "model_version": "model-v1",
                    "decision_rule_version": "rules-v1",
                }
            )
        for axis_name in (
            "overall_risk",
            "capability_breadth",
            "network_egress",
        ):
            rows.append(
                {
                    "server_id": "server-2",
                    "axis_name": axis_name,
                    "p_top": 0.4,
                    "scored_at": "2025-01-02T00:00:00+00:00",
                    "model_version": "model-v1",
                    "decision_rule_version": "rules-v1",
                }
            )

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    server_id,
                    axis_name,
                    p_top,
                    scored_at,
                    model_version,
                    decision_rule_version
                ) VALUES (
                    :server_id,
                    :axis_name,
                    :p_top,
                    :scored_at,
                    :model_version,
                    :decision_rule_version
                )
                """
            ),
            rows,
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scoring/coverage-gaps")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 7
        by_axis = {item["axis_name"]: item for item in payload["axes"]}
        assert by_axis["auth_strength"]["servers_missing"] == 2
        assert payload["never_scored_servers"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()