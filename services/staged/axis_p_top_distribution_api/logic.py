from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

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

BUCKETS = [index / 10 for index in range(11)]


def _bucket_expression() -> Any:
    return case(
        (McpLlmAxisScore.p_top < 0.1, 0),
        (McpLlmAxisScore.p_top < 0.2, 1),
        (McpLlmAxisScore.p_top < 0.3, 2),
        (McpLlmAxisScore.p_top < 0.4, 3),
        (McpLlmAxisScore.p_top < 0.5, 4),
        (McpLlmAxisScore.p_top < 0.6, 5),
        (McpLlmAxisScore.p_top < 0.7, 6),
        (McpLlmAxisScore.p_top < 0.8, 7),
        (McpLlmAxisScore.p_top < 0.9, 8),
        else_=9,
    )


def get_p_top_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    bucket = _bucket_expression()

    histogram_columns = [
        func.coalesce(
            func.sum(case((bucket == index, 1), else_=0)),
            0,
        ).label(f"bucket_{index}")
        for index in range(10)
    ]

    statement = (
        select(
            McpLlmAxisScore.axis_name.label("axis_name"),
            func.count(McpLlmAxisScore.p_top).label("count"),
            func.min(McpLlmAxisScore.p_top).label("min_p_top"),
            func.max(McpLlmAxisScore.p_top).label("max_p_top"),
            func.avg(McpLlmAxisScore.p_top).label("mean_p_top"),
            func.coalesce(
                func.sum(
                    case(
                        (McpLlmAxisScore.escalated.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("escalated_count"),
            *histogram_columns,
        )
        .where(
            McpLlmAxisScore.axis_name.in_(AXIS_NAMES),
            McpLlmAxisScore.p_top.is_not(None),
        )
        .group_by(McpLlmAxisScore.axis_name)
    )

    rows = session.execute(statement).mappings().all()

    axes: List[Dict[str, Any]] = []
    for row in rows:
        axes.append(
            {
                "axis_name": row["axis_name"],
                "count": int(row["count"] or 0),
                "min_p_top": row["min_p_top"],
                "max_p_top": row["max_p_top"],
                "mean_p_top": row["mean_p_top"],
                "escalated_count": int(row["escalated_count"] or 0),
                "histogram": [int(row[f"bucket_{index}"] or 0) for index in range(10)],
            }
        )

    axes.sort(key=lambda item: item["axis_name"])

    return {
        "generated_at": datetime.now(timezone.utc),
        "buckets": BUCKETS,
        "axes": axes,
    }


def calculate_p_top_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_p_top_distribution(session)


def _self_test() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    label VARCHAR,
                    label_index INTEGER,
                    probs TEXT,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )

        rows = []
        for server_number in range(3):
            for axis_name in AXIS_NAMES[:3]:
                for p_top in (0.05, 0.5, 0.95):
                    rows.append(
                        {
                            "server_id": f"server-{server_number}",
                            "axis_name": axis_name,
                            "p_top": p_top,
                            "escalated": p_top >= 0.9,
                        }
                    )

        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, p_top, escalated)
                VALUES
                    (:server_id, :axis_name, :p_top, :escalated)
                """
            ),
            rows,
        )

    def override_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/axes/p-top-distribution")
    def p_top_distribution(session: Session = Depends(get_session)):
        return get_p_top_distribution(session)

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/p-top-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 3
        for axis in payload["axes"]:
            assert sum(axis["histogram"]) == axis["count"]
        assert payload["buckets"] == BUCKETS

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()