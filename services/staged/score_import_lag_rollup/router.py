from __future__ import annotations

from datetime import date, datetime, timedelta
from inspect import isawaitable
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_import_lag_rollup

router = APIRouter(prefix="/api", tags=["scores"])


class ImportLagPoint(BaseModel):
    date: date
    servers_scored: int
    median_lag_hours: Optional[float]
    stale_servers: int


class ImportLagResponse(BaseModel):
    days: int
    series: list[ImportLagPoint]


def _extract_series(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, dict):
        series = result.get("series", [])
    elif hasattr(result, "model_dump"):
        series = result.model_dump().get("series", [])
    elif hasattr(result, "__dict__"):
        series = getattr(result, "series", [])
    else:
        series = result
    return list(series or [])


@router.get("/scores/import-lag", response_model=ImportLagResponse)
async def score_import_lag(
    days: int = Query(7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> ImportLagResponse:
    result = get_import_lag_rollup(session, days)
    if isawaitable(result):
        result = await result
    return ImportLagResponse(days=days, series=_extract_series(result))


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = McpLlmAxisScore.metadata
    metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )

    today = datetime.utcnow().replace(hour=11, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    with Session(engine) as session:
        for index in range(1, 4):
            session.add(
                McpServerRegistry(
                    server_id=f"test-server-{index}",
                    first_seen=yesterday - timedelta(days=1),
                    last_scanned=today - timedelta(hours=1),
                    scan_count=3,
                )
            )

        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="test-server-1",
                    axis_name="security",
                    model_version="test-model",
                    p_top=0.1,
                    scored_at=today,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="test-server-2",
                    axis_name="security",
                    model_version="test-model",
                    p_top=0.2,
                    scored_at=today,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="test-server-3",
                    axis_name="security",
                    model_version="test-model",
                    p_top=0.3,
                    scored_at=yesterday,
                ),
            ]
        )
        session.commit()

        def override_get_session() -> Session:
            return session

        from fastapi import FastAPI

        local_app = FastAPI()
        local_app.include_router(router)
        local_app.dependency_overrides[get_session] = override_get_session

        with TestClient(local_app) as client:
            response = client.get("/api/scores/import-lag", params={"days": 2})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["series"]) == 2, payload
            assert any(
                point["median_lag_hours"] == 1.0 for point in payload["series"]
            ), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()