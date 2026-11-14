"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import axis_heatmap
except ImportError:
    try:
        from .logic import get_axis_heatmap as axis_heatmap
    except ImportError:
        from .logic import build_axis_heatmap as axis_heatmap


router = APIRouter(prefix="/api/axis", tags=["axis"])


class AxisHeatRow(BaseModel):
    axis_name: str
    label: str
    server_count: int
    mean_p_top: float
    max_p_top: float
    critical_count: int
    danger_count: int
    escalated_count: int


class AxisHeatmapResponse(BaseModel):
    tier: str | None
    days: int
    as_of: str
    axes: list[AxisHeatRow]


def _response_rows(result: Any) -> list[AxisHeatRow]:
    if isinstance(result, dict):
        rows = result.get("axes", [])
    else:
        rows = getattr(result, "axes", result)

    if not isinstance(rows, list):
        rows = list(rows or [])

    return [
        row
        if isinstance(row, AxisHeatRow)
        else AxisHeatRow.model_validate(row)
        for row in rows
    ]


@router.get("/heatmap", response_model=AxisHeatmapResponse)
def get_axis_heatmap(
    tier: str | None = Query(default=None),
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> AxisHeatmapResponse:
    result = axis_heatmap(db, tier=tier, days=days)

    if isinstance(result, dict):
        as_of = result.get("as_of")
    else:
        as_of = getattr(result, "as_of", None)

    if as_of is None:
        as_of = datetime.now(timezone.utc).isoformat()
    elif not isinstance(as_of, str):
        as_of = as_of.isoformat()

    return AxisHeatmapResponse(
        tier=tier,
        days=days,
        as_of=as_of,
        axes=_response_rows(result),
    )


def _self_test() -> None:
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
    McpServerRegistry.__table__.create(bind=engine)
    McpLlmAxisScore.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = lambda: session_factory()

    try:
        with session_factory() as db:
            db.add_all(
                [
                    McpServerRegistry(
                        server_id="self-test-1",
                        name="Self Test Server 1",
                        risk_tier="tier-1",
                    ),
                    McpServerRegistry(
                        server_id="self-test-2",
                        name="Self Test Server 2",
                        risk_tier="tier-1",
                    ),
                ]
            )
            db.flush()

            axes = [
                "overall_risk",
                "auth_strength",
                "capability_breadth",
                "data_sensitivity",
                "network_egress",
                "maintainer_trust",
                "exploit_surface",
            ]
            rows = []
            score_id = 1
            for server_id, p_top, p_critical, p_danger in (
                ("self-test-1", 0.2, 0.6, 0.7),
                ("self-test-2", 0.4, 0.2, 0.3),
            ):
                for axis_name in axes:
                    rows.append(
                        McpLlmAxisScore(
                            id=score_id,
                            server_id=server_id,
                            axis_name=axis_name,
                            label=axis_name.replace("_", " ").title(),
                            model_version="self-test",
                            p_top=p_top,
                            p_critical=p_critical,
                            p_danger=p_danger,
                            escalated=False,
                            scored_at=datetime.now(timezone.utc),
                        )
                    )
                    score_id += 1
            db.add_all(rows)
            db.commit()

        with TestClient(local_app) as client:
            response = client.get("/api/axis/heatmap")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["axes"]) == 7

            overall = next(
                row for row in payload["axes"] if row["axis_name"] == "overall_risk"
            )
            assert abs(overall["mean_p_top"] - 0.3) <= 1e-6
            assert overall["critical_count"] == 1

        print("PASS")
    finally:
        local_app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    _self_test()