from __future__ import annotations

from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


def _quantile(sorted_values: list[float], probability: float) -> float:
    """Return a linearly interpolated quantile for a non-empty sorted list."""
    if not sorted_values:
        raise ValueError("Cannot calculate a quantile for an empty list")

    position = (len(sorted_values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index

    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + (upper_value - lower_value) * fraction


def _calculate_label_quantiles(
    label: str,
    label_index: int,
    p_top_values: list[Any],
) -> dict[str, Any]:
    values = sorted(float(value) for value in p_top_values if value is not None)
    if not values:
        raise ValueError(f"No p_top values were found for label {label!r}")

    return {
        "label": label,
        "label_index": label_index,
        "count": len(values),
        "p10": _quantile(values, 0.10),
        "p50": _quantile(values, 0.50),
        "p90": _quantile(values, 0.90),
        "min": values[0],
        "max": values[-1],
    }


@router.get("/api/scoring/axis-p-top-quantiles")
def get_axis_p_top_quantiles(
    axis_name: str = Query(default="overall_risk"),
    limit: int = Query(default=25, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    label_rows = session.execute(
        select(McpLlmAxisScore.label)
        .where(McpLlmAxisScore.axis_name == axis_name)
        .group_by(McpLlmAxisScore.label)
        .order_by(McpLlmAxisScore.label)
        .limit(limit)
    ).scalars().all()

    selected_labels = [str(label) for label in label_rows if label is not None]
    if not selected_labels:
        return {"axis_name": axis_name, "quantiles": []}

    value_rows = session.execute(
        select(McpLlmAxisScore.label, McpLlmAxisScore.p_top)
        .where(McpLlmAxisScore.axis_name == axis_name)
        .where(McpLlmAxisScore.label.in_(selected_labels))
    ).all()

    values_by_label: dict[str, list[Any]] = {label: [] for label in selected_labels}
    for label, p_top in value_rows:
        if label is None or p_top is None:
            continue
        values_by_label.setdefault(str(label), []).append(p_top)

    quantiles = [
        _calculate_label_quantiles(label, label_index, values_by_label.get(label, []))
        for label_index, label in enumerate(selected_labels)
        if values_by_label.get(label)
    ]

    return {"axis_name": axis_name, "quantiles": quantiles}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SqlAlchemySession
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    p_top REAL NOT NULL,
                    p_critical REAL,
                    p_danger REAL,
                    scored_at TEXT NOT NULL
                )
                """
            )
        )

        rows = [
            {
                "server_id": f"server-{label.lower()}-{index}",
                "axis_name": "overall_risk",
                "label": label,
                "p_top": p_top,
                "p_critical": 0.5,
                "p_danger": 0.8,
                "scored_at": "2025-01-01T00:00:00Z",
            }
            for label, values in {
                "A": [0.1, 0.2, 0.3, 0.4],
                "B": [0.2, 0.4, 0.6, 0.8],
                "C": [0.3, 0.5, 0.7, 0.9],
            }.items()
            for index, p_top in enumerate(values)
        ]
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    server_id,
                    axis_name,
                    label,
                    p_top,
                    p_critical,
                    p_danger,
                    scored_at
                ) VALUES (
                    :server_id,
                    :axis_name,
                    :label,
                    :p_top,
                    :p_critical,
                    :p_danger,
                    :scored_at
                )
                """
            ),
            rows,
        )

    def override_get_session():
        with SqlAlchemySession(test_engine) as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/scoring/axis-p-top-quantiles",
            params={"axis_name": "overall_risk", "limit": 25},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["axis_name"] == "overall_risk"
        assert len(payload["quantiles"]) == 3

        quantiles_by_label = {
            item["label"]: item for item in payload["quantiles"]
        }
        assert set(quantiles_by_label) == {"A", "B", "C"}
        assert quantiles_by_label["A"]["count"] == 4
        assert quantiles_by_label["A"]["p50"] == median([0.1, 0.2, 0.3, 0.4])
        assert quantiles_by_label["A"]["min"] == 0.1
        assert quantiles_by_label["A"]["max"] == 0.4

    print("PASS")