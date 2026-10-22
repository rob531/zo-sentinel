from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func

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
    axis: Optional[str]
    buckets: List[str]
    rows: List[HeatmapRow]
    generated_at: str


router = APIRouter(prefix="/api")


def get_axis_heatmap(session, axis: Optional[str] = None) -> AxisHeatmapResponse:
    score = McpLlmAxisScore
    server = McpServerRegistry

    query = (
        session.query(
            score.axis_name.label("axis_name"),
            score.label.label("label"),
            score.label_index.label("label_index"),
            func.avg(score.p_top).label("mean_p_top"),
            func.count(score.id).label("score_count"),
        )
        .join(server, score.server_id == server.server_id)
    )
    if axis is not None:
        query = query.filter(score.axis_name == axis)

    records = (
        query.group_by(
            score.axis_name,
            score.label,
            score.label_index,
        )
        .order_by(
            score.axis_name,
            score.label_index,
            score.label,
        )
        .all()
    )

    rows: List[HeatmapRow] = []
    current_axis: Optional[str] = None
    current_cells: List[HeatmapCell] = []
    max_label_index = -1

    for record in records:
        label_index = int(record.label_index) if record.label_index is not None else 0
        max_label_index = max(max_label_index, label_index)

        if current_axis is not None and record.axis_name != current_axis:
            rows.append(
                HeatmapRow(axis_name=current_axis, cells=current_cells)
            )
            current_cells = []

        current_axis = record.axis_name
        current_cells.append(
            HeatmapCell(
                label=str(record.label),
                mean_p_top=float(record.mean_p_top),
                count=int(record.score_count),
            )
        )

    if current_axis is not None:
        rows.append(HeatmapRow(axis_name=current_axis, cells=current_cells))

    buckets = [
        f"label_{index}"
        for index in range(max_label_index + 1)
    ]

    return AxisHeatmapResponse(
        axis=axis,
        buckets=buckets,
        rows=rows,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


build_axis_heatmap = get_axis_heatmap


@router.get("/axis/heatmap", response_model=AxisHeatmapResponse)
def read_axis_heatmap(
    axis: Optional[str] = None,
    session=Depends(get_session),
) -> AxisHeatmapResponse:
    return get_axis_heatmap(session=session, axis=axis)


def create_app() -> FastAPI:
    application = FastAPI(title="axis_p_top_heatmap_api")
    application.include_router(router)
    return application


app = create_app()


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    axis_name VARCHAR,
                    label VARCHAR,
                    label_index INTEGER,
                    p_top FLOAT,
                    server_id VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        for index in range(1, 4):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier)
                    VALUES
                        (:server_id, :name, :risk_tier)
                    """
                ),
                {
                    "server_id": f"server-{index}",
                    "name": f"Server {index}",
                    "risk_tier": "tier_1",
                },
            )

        scores = [
            ("server-1", "auth_strength", "safe", 0, 0.4),
            ("server-2", "auth_strength", "safe", 0, 0.6),
            ("server-3", "auth_strength", "safe", 0, 0.8),
            ("server-1", "exposure", "low", 0, 0.2),
            ("server-2", "exposure", "low", 0, 0.4),
            ("server-3", "exposure", "low", 0, 0.6),
        ]
        for server_id, axis_name, label, label_index, p_top in scores:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (axis_name, label, label_index, p_top, server_id, scored_at)
                    VALUES
                        (:axis_name, :label, :label_index, :p_top, :server_id, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "axis_name": axis_name,
                    "label": label,
                    "label_index": label_index,
                    "p_top": p_top,
                    "server_id": server_id,
                },
            )

    session = Session()
    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/heatmap")
        assert response.status_code == 200
        payload = response.json()
        assert {row["axis_name"] for row in payload["rows"]} == {
            "auth_strength",
            "exposure",
        }
        auth_row = next(
            row for row in payload["rows"]
            if row["axis_name"] == "auth_strength"
        )
        assert abs(auth_row["cells"][0]["mean_p_top"] - 0.6) < 1e-9
        assert auth_row["cells"][0]["count"] == 3

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()