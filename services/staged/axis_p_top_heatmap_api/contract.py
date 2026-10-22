from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class HeatmapCell(BaseModel):
    label: str
    mean_p_top: float
    count: int


class HeatmapRow(BaseModel):
    axis_name: str
    cells: List[HeatmapCell]


class AxisHeatmapResponse(BaseModel):
    axis: Optional[str] = None
    buckets: List[str] = Field(default_factory=list)
    rows: List[HeatmapRow] = Field(default_factory=list)
    generated_at: str


router = APIRouter(prefix="/api")


@router.get("/axis/heatmap", response_model=AxisHeatmapResponse)
def get_axis_heatmap(
    axis: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> AxisHeatmapResponse:
    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.min(McpLlmAxisScore.label_index).label("bucket_index"),
            func.avg(McpLlmAxisScore.p_top).label("mean_p_top"),
            func.count(McpLlmAxisScore.server_id).label("cell_count"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )
    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    statement = statement.group_by(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
    ).order_by(
        McpLlmAxisScore.axis_name,
        func.min(McpLlmAxisScore.label_index),
        McpLlmAxisScore.label,
    )

    aggregated = session.execute(statement).all()

    bucket_positions: dict[str, int] = {}
    for row in aggregated:
        label = str(row.label)
        position = int(row.bucket_index) if row.bucket_index is not None else 0
        if label not in bucket_positions or position < bucket_positions[label]:
            bucket_positions[label] = position

    buckets = sorted(bucket_positions, key=lambda label: (bucket_positions[label], label))
    cells_by_axis: dict[str, list[HeatmapCell]] = defaultdict(list)
    for row in aggregated:
        cells_by_axis[str(row.axis_name)].append(
            HeatmapCell(
                label=str(row.label),
                mean_p_top=float(row.mean_p_top),
                count=int(row.cell_count),
            )
        )

    rows = []
    for axis_name in sorted(cells_by_axis):
        cells_by_label = {cell.label: cell for cell in cells_by_axis[axis_name]}
        rows.append(
            HeatmapRow(
                axis_name=axis_name,
                cells=[
                    cells_by_label[label]
                    for label in buckets
                    if label in cells_by_label
                ],
            )
        )

    return AxisHeatmapResponse(
        axis=axis,
        buckets=buckets,
        rows=rows,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Axis P-Top Heatmap API")
    application.include_router(router)
    return application


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    risk_tier VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    label_index INTEGER NOT NULL,
                    p_top FLOAT NOT NULL,
                    server_id VARCHAR NOT NULL,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES ('server-1', 'Server One', 'low'),
                       ('server-2', 'Server Two', 'medium'),
                       ('server-3', 'Server Three', 'high')
                """
            )
        )

        scored_at = datetime.now(timezone.utc).isoformat()
        scores = []
        values = {
            ("auth_strength", "label_0"): (0.40, 0.44, 0.42),
            ("auth_strength", "label_1"): (0.70, 0.80, 0.90),
            ("privacy", "label_0"): (0.30, 0.40, 0.50),
            ("privacy", "label_1"): (0.60, 0.65, 0.70),
        }
        score_id = 1
        for (axis_name, label), p_top_values in values.items():
            for index, p_top in enumerate(p_top_values, start=1):
                scores.append(
                    {
                        "id": score_id,
                        "axis_name": axis_name,
                        "label": label,
                        "label_index": 0 if label == "label_0" else 1,
                        "p_top": p_top,
                        "server_id": f"server-{index}",
                        "scored_at": scored_at,
                    }
                )
                score_id += 1
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, axis_name, label, label_index, p_top, server_id, scored_at)
                VALUES
                    (:id, :axis_name, :label, :label_index, :p_top, :server_id, :scored_at)
                """
            ),
            scores,
        )

    session = testing_session()
    application = create_app()

    def override_get_session():
        return session

    application.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(application) as client:
            response = client.get("/api/axis/heatmap")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["axis"] is None
            assert payload["buckets"] == ["label_0", "label_1"]

            rows = {row["axis_name"]: row for row in payload["rows"]}
            assert set(rows) == {"auth_strength", "privacy"}

            auth_cells = {cell["label"]: cell for cell in rows["auth_strength"]["cells"]}
            assert auth_cells["label_0"]["count"] == 3
            assert abs(auth_cells["label_0"]["mean_p_top"] - 0.42) < 1e-9

            privacy_cells = {cell["label"]: cell for cell in rows["privacy"]["cells"]}
            assert privacy_cells["label_0"]["count"] == 3
            assert abs(privacy_cells["label_0"]["mean_p_top"] - 0.40) < 1e-9

            filtered_response = client.get(
                "/api/axis/heatmap",
                params={"axis": "auth_strength"},
            )
            assert filtered_response.status_code == 200, filtered_response.text
            filtered_payload = filtered_response.json()
            assert filtered_payload["axis"] == "auth_strength"
            assert len(filtered_payload["rows"]) == 1
            assert filtered_payload["rows"][0]["axis_name"] == "auth_strength"
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()