from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

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


class AxisFreshness(BaseModel):
    axis_name: str
    servers_scored: int
    row_count: int
    stale_servers: int
    median_staleness_days: Optional[float]


class AxisFreshnessResponse(BaseModel):
    days: int
    generated_at: datetime
    axes: list[AxisFreshness]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_axis_freshness(
    session: Session,
    days: int,
    generated_at: Optional[datetime] = None,
) -> AxisFreshnessResponse:
    now = _as_utc(generated_at or datetime.now(timezone.utc))
    cutoff = now - timedelta(days=days)

    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.p_top,
        ).where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
    ).all()

    active_by_axis = {axis_name: [] for axis_name in AXIS_NAMES}
    latest_by_axis_server = {
        axis_name: {} for axis_name in AXIS_NAMES
    }

    for server_id, axis_name, scored_at, _ in rows:
        scored_at_utc = _as_utc(scored_at)
        latest = latest_by_axis_server[axis_name].get(server_id)
        if latest is None or scored_at_utc > latest:
            latest_by_axis_server[axis_name][server_id] = scored_at_utc
        if cutoff <= scored_at_utc <= now:
            active_by_axis[axis_name].append((server_id, scored_at_utc))

    axes: list[AxisFreshness] = []
    for axis_name in AXIS_NAMES:
        active_rows = active_by_axis[axis_name]
        servers_scored = len({server_id for server_id, _ in active_rows})
        row_count = len(active_rows)
        stale_servers = sum(
            1
            for latest_scored_at in latest_by_axis_server[axis_name].values()
            if latest_scored_at < cutoff
        )
        staleness_days = [
            (now - scored_at).total_seconds() / 86400.0
            for _, scored_at in active_rows
        ]
        median_staleness_days = (
            round(float(statistics.median(staleness_days)), 6)
            if staleness_days
            else None
        )
        axes.append(
            AxisFreshness(
                axis_name=axis_name,
                servers_scored=servers_scored,
                row_count=row_count,
                stale_servers=stale_servers,
                median_staleness_days=median_staleness_days,
            )
        )

    return AxisFreshnessResponse(
        days=days,
        generated_at=now,
        axes=axes,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/axes/freshness",
    response_model=AxisFreshnessResponse,
)
def axis_freshness(
    days: int = Query(..., ge=1, le=3650),
    session: Session = Depends(get_session),
) -> AxisFreshnessResponse:
    return get_axis_freshness(session=session, days=days)


app = FastAPI(title="axis_freshness_watch_api")
app.include_router(router)


def run_acceptance_test() -> None:
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
                CREATE TABLE mcp_llm_axis_scores (
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    scored_at DATETIME NOT NULL,
                    p_top FLOAT
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).replace(tzinfo=None)
    older = (now - timedelta(days=2)).replace(tzinfo=None)

    seed_rows = [
        ("server-1", "overall_risk", recent, 0.91),
        ("server-2", "overall_risk", older, 0.73),
        ("server-3", "overall_risk", recent, 0.84),
        ("server-1", "auth_strength", older, 0.62),
        ("server-2", "auth_strength", recent, 0.79),
        ("server-3", "auth_strength", older, 0.68),
    ]

    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, scored_at, p_top)
                VALUES
                    (:server_id, :axis_name, :scored_at, :p_top)
                """
            ),
            [
                {
                    "server_id": server_id,
                    "axis_name": axis_name,
                    "scored_at": scored_at,
                    "p_top": p_top,
                }
                for server_id, axis_name, scored_at, p_top in seed_rows
            ],
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/axes/freshness", params={"days": 30})

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert len(payload["axes"]) == 7

        axes = {axis["axis_name"]: axis for axis in payload["axes"]}
        assert axes["overall_risk"]["servers_scored"] == 3
        assert axes["overall_risk"]["row_count"] == 3
        assert axes["overall_risk"]["median_staleness_days"] is not None
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_acceptance_test()