"""HTTP surface for the escalated axis heatmap service."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import escalated_axis_heatmap

router = APIRouter(prefix="/api", tags=["risk"])


class EscalatedAxisCount(BaseModel):
    axis_name: str
    escalated_to: str | None
    count: int


class EscalatedAxisHeatmapResponse(BaseModel):
    axes: list[EscalatedAxisCount]
    total_escalations: int


def _response_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        axes = result.get("axes", [])
        total = result.get("total_escalations")
    elif isinstance(result, (tuple, list)) and len(result) == 2:
        axes, total = result
    else:
        axes = getattr(result, "axes", [])
        total = getattr(result, "total_escalations", None)

    if hasattr(axes, "model_dump"):
        axes = axes.model_dump()
    elif hasattr(axes, "dict"):
        axes = axes.dict()

    normalized_axes = list(axes or [])
    if total is None:
        total = sum(
            int(item.get("count", 0))
            if isinstance(item, dict)
            else int(getattr(item, "count", 0))
            for item in normalized_axes
        )

    return {
        "axes": normalized_axes,
        "total_escalations": int(total),
    }


@router.get(
    "/risk/escalated-axis-heatmap",
    response_model=EscalatedAxisHeatmapResponse,
)
def get_escalated_axis_heatmap(
    db: Session = Depends(get_session),
) -> EscalatedAxisHeatmapResponse:
    result = escalated_axis_heatmap(db)
    return EscalatedAxisHeatmapResponse.model_validate(_response_payload(result))


def _identifier(table: Any, column_name: str, value: int) -> int | str:
    column = table.columns[column_name]
    if isinstance(column.type, Integer):
        return value
    return f"heat-{value}"


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    with Session(engine) as session:
        server_ids: list[int | str] = []
        for index in range(1, 4):
            server_id = _identifier(McpServerRegistry.__table__, "server_id", index)
            server_ids.append(server_id)
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"heat-server-{index}",
                    url=f"https://heat-{index}.example",
                )
            )

        seeded = [
            (0, "overall_risk", "critical"),
            (1, "overall_risk", "critical"),
            (2, "auth_strength", "high"),
        ]
        for index, (server_index, axis_name, escalated_to) in enumerate(seeded, start=1):
            session.add(
                McpLlmAxisScore(
                    id=_identifier(McpLlmAxisScore.__table__, "id", index),
                    server_id=server_ids[server_index],
                    axis_name=axis_name,
                    model_version="heat-test-v1",
                    escalated=True,
                    escalated_to=escalated_to,
                )
            )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk/escalated-axis-heatmap")
        assert response.status_code == 200, response.text
        payload = response.json()
        counts = {
            item["axis_name"]: item["count"]
            for item in payload["axes"]
        }
        assert counts.get("overall_risk") == 2, payload
        assert counts.get("auth_strength") == 1, payload
        assert payload["total_escalations"] == 3, payload

    engine.dispose()
    print("PASS")