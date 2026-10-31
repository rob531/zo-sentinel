from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


EXPECTED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_axis_label_index_map(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return label ordering and coverage for the canonical risk axes."""
    score_table = McpLlmAxisScore.__table__

    statement = (
        select(
            score_table.c.axis_name,
            score_table.c.label,
            func.min(score_table.c.label_index).label("label_index_min"),
            func.max(score_table.c.label_index).label("label_index_max"),
            func.count(func.distinct(score_table.c.server_id)).label(
                "distinct_servers"
            ),
            func.count().label("rows"),
        )
        .group_by(score_table.c.axis_name, score_table.c.label)
    )

    rows = []
    for row in session.execute(statement):
        axis_name = row.axis_name
        label_index_min = row.label_index_min
        label_index_max = row.label_index_max
        rows.append(
            {
                "axis_name": axis_name,
                "label": row.label,
                "label_index_min": (
                    int(label_index_min)
                    if label_index_min is not None
                    else None
                ),
                "label_index_max": (
                    int(label_index_max)
                    if label_index_max is not None
                    else None
                ),
                "distinct_servers": int(row.distinct_servers or 0),
                "rows": int(row.rows or 0),
            }
        )

    expected_axis_order = {axis: index for index, axis in enumerate(EXPECTED_AXES)}
    rows.sort(
        key=lambda row: (
            expected_axis_order.get(row["axis_name"], len(EXPECTED_AXES)),
            row["axis_name"] or "",
            row["label_index_min"]
            if row["label_index_min"] is not None
            else 0,
            row["label"] or "",
        )
    )

    present_axes = {row["axis_name"] for row in rows}
    missing_axes = [axis for axis in EXPECTED_AXES if axis not in present_axes]

    return {
        "axes": rows,
        "expected_axes": list(EXPECTED_AXES),
        "missing_axes": missing_axes,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as setup_session:
        setup_session.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    scored_at TEXT,
                    server_id TEXT
                )
                """
            )
        )
        setup_session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT
                )
                """
            )
        )

        for server_id in ("server-1", "server-2", "server-3"):
            setup_session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (server_id)
                    VALUES (:server_id)
                    """
                ),
                {"server_id": server_id},
            )

        scored_axes = (
            ("overall_risk", "critical", 0),
            ("auth_strength", "weak", 0),
            ("capability_breadth", "broad", 0),
        )
        for server_id in ("server-1", "server-2", "server-3"):
            for axis_name, label, label_index in scored_axes:
                setup_session.execute(
                    text(
                        """
                        INSERT INTO mcp_llm_axis_scores
                            (axis_name, label, label_index, scored_at, server_id)
                        VALUES
                            (:axis_name, :label, :label_index, :scored_at, :server_id)
                        """
                    ),
                    {
                        "axis_name": axis_name,
                        "label": label,
                        "label_index": label_index,
                        "scored_at": "2024-01-01T00:00:00",
                        "server_id": server_id,
                    },
                )
        setup_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()

    @test_app.get("/api/axis/label-index-map")
    def axis_label_index_map_route(
        result: dict[str, Any] = Depends(get_axis_label_index_map),
    ):
        return result

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/label-index-map")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["expected_axes"]) == 7
        assert "data_sensitivity" in payload["missing_axes"]
        assert any(
            row["axis_name"] == "overall_risk"
            and row["distinct_servers"] == 3
            for row in payload["axes"]
        )

    print("PASS")