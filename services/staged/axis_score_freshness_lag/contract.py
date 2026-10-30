from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
_VALID_AXIS_SET = frozenset(VALID_AXIS_NAMES)


class AxisFreshnessLag(BaseModel):
    axis_name: str
    oldest_scored_at: Optional[str]
    newest_scored_at: Optional[str]
    scored_count: int
    stale_24h_count: int
    stale_7d_count: int
    lag_seconds_p50: Optional[int]


class AxisFreshnessLagResponse(BaseModel):
    as_of: str
    axes: List[AxisFreshnessLag]


router = APIRouter(prefix="/api")
app = FastAPI()
app.include_router(router)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _lag_seconds(scored_at: datetime, as_of: datetime) -> int:
    return max(0, int((as_of - _as_utc(scored_at)).total_seconds()))


def _percentile_50(values: List[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[midpoint])
    return int(round((ordered[midpoint - 1] + ordered[midpoint]) / 2))


@router.get("/axis/freshness-lag", response_model=AxisFreshnessLagResponse)
def get_axis_freshness_lag(
    axis_name: Optional[str] = None,
    session: Session = Depends(get_session),
) -> AxisFreshnessLagResponse:
    as_of = datetime.now(timezone.utc)
    if axis_name is not None and axis_name not in _VALID_AXIS_SET:
        return AxisFreshnessLagResponse(as_of=_iso8601(as_of), axes=[])

    statement = (
        __import__("sqlalchemy").select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
        )
        .where(McpLlmAxisScore.axis_name.in_(VALID_AXIS_NAMES))
    )
    if axis_name is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis_name)

    grouped: Dict[str, List[datetime]] = {}
    for name, scored_at, _server_id, _p_top in session.execute(statement).all():
        if name in _VALID_AXIS_SET:
            grouped.setdefault(name, []).append(scored_at)

    axes: List[AxisFreshnessLag] = []
    for name in VALID_AXIS_NAMES:
        scored_times = grouped.get(name)
        if not scored_times:
            continue

        dated_scores = [value for value in scored_times if value is not None]
        lags = [_lag_seconds(value, as_of) for value in dated_scores]
        oldest = min(dated_scores) if dated_scores else None
        newest = max(dated_scores) if dated_scores else None

        axes.append(
            AxisFreshnessLag(
                axis_name=name,
                oldest_scored_at=_iso8601(oldest),
                newest_scored_at=_iso8601(newest),
                scored_count=len(scored_times),
                stale_24h_count=sum(1 for lag in lags if lag >= 24 * 60 * 60),
                stale_7d_count=sum(1 for lag in lags if lag >= 7 * 24 * 60 * 60),
                lag_seconds_p50=_percentile_50(lags),
            )
        )

    return AxisFreshnessLagResponse(as_of=_iso8601(as_of), axes=axes)


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session = Session(engine)

    session.execute(
        text(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                axis_name VARCHAR NOT NULL,
                scored_at DATETIME,
                server_id VARCHAR NOT NULL,
                p_top FLOAT,
                model_version VARCHAR NOT NULL
            )
            """
        )
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    row_id = 1
    for axis_name in ("overall_risk", "auth_strength"):
        for age in (0, 2, 30):
            rows.append(
                {
                    "id": row_id,
                    "axis_name": axis_name,
                    "scored_at": now - __import__("datetime").timedelta(days=age),
                    "server_id": f"server-{axis_name}-{age}",
                    "p_top": 0.5,
                    "model_version": "test-model",
                }
            )
            row_id += 1

    session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores
                (id, axis_name, scored_at, server_id, p_top, model_version)
            VALUES
                (:id, :axis_name, :scored_at, :server_id, :p_top, :model_version)
            """
        ),
        rows,
    )
    session.commit()

    def override_get_session():
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/freshness-lag")
        assert response.status_code == 200
        payload = response.json()
        axes = payload["axes"]
        axis_names = {axis["axis_name"] for axis in axes}
        assert "overall_risk" in axis_names
        assert "auth_strength" in axis_names

        for axis in axes:
            assert axis["scored_count"] == 3
            assert axis["stale_7d_count"] >= 1
            assert axis["lag_seconds_p50"] is None or isinstance(
                axis["lag_seconds_p50"], int
            )

        unknown = client.get(
            "/api/axis/freshness-lag",
            params={"axis_name": "not_an_axis"},
        )
        assert unknown.status_code == 200
        assert unknown.json()["axes"] == []

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()