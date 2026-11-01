from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, StrictInt, StrictStr
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


BUCKETS = (
    "lt_1h",
    "lt_1d",
    "lt_7d",
    "lt_30d",
    "gt_30d",
)


class StalenessBucket(BaseModel):
    bucket: str
    count: StrictInt


class AxisStalenessLag(BaseModel):
    axis_name: StrictStr
    buckets: List[StalenessBucket]
    stale_total: StrictInt
    oldest_scored_at: Optional[StrictStr]


router = APIRouter(prefix="/api", tags=["scores"])


def _normalise_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("Unsupported timestamp value")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _isoformat(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalise_datetime(value).isoformat()
    return _normalise_datetime(value).isoformat()


def _bucket_for_age(age: timedelta) -> str:
    if age < timedelta(hours=1):
        return "lt_1h"
    if age < timedelta(days=1):
        return "lt_1d"
    if age < timedelta(days=7):
        return "lt_7d"
    if age <= timedelta(days=30):
        return "lt_30d"
    return "gt_30d"


def get_axis_score_staleness_lag(session=Depends(get_session)) -> List[AxisStalenessLag]:
    now = datetime.now(timezone.utc)
    query = text(
        """
        SELECT
            s.axis_name,
            s.scored_at,
            s.model_version,
            r.last_seen,
            r.last_scanned
        FROM mcp_llm_axis_scores AS s
        INNER JOIN mcp_server_registry AS r
            ON r.server_id = s.server_id
        WHERE s.scored_at IS NOT NULL
        ORDER BY s.axis_name, s.scored_at
        """
    )
    rows = session.execute(query).mappings().all()

    grouped: Dict[str, List[datetime]] = {}
    for row in rows:
        axis_name = str(row["axis_name"])
        scored_at = _normalise_datetime(row["scored_at"])
        grouped.setdefault(axis_name, []).append(scored_at)

    response: List[AxisStalenessLag] = []
    for axis_name in sorted(grouped):
        scores = grouped[axis_name]
        counts = {bucket: 0 for bucket in BUCKETS}
        for scored_at in scores:
            counts[_bucket_for_age(now - scored_at)] += 1

        oldest = min(scores) if scores else None
        response.append(
            AxisStalenessLag(
                axis_name=axis_name,
                buckets=[
                    StalenessBucket(bucket=bucket, count=counts[bucket])
                    for bucket in BUCKETS
                ],
                stale_total=counts["gt_30d"],
                oldest_scored_at=_isoformat(oldest),
            )
        )
    return response


get_staleness_lag = get_axis_score_staleness_lag


@router.get(
    "/scores/staleness-lag",
    response_model=List[AxisStalenessLag],
)
def get_staleness_lag_route(
    result: List[AxisStalenessLag] = Depends(get_axis_score_staleness_lag),
) -> List[AxisStalenessLag]:
    return result


def _create_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    last_seen DATETIME,
                    last_scanned DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    model_version TEXT,
                    scored_at DATETIME
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    seeded_ages = [
        timedelta(minutes=10),
        timedelta(days=2),
        timedelta(days=40),
    ]

    with engine.begin() as connection:
        for index, age in enumerate(seeded_ages, start=1):
            server_id = f"server-{index}"
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, last_seen, last_scanned)
                    VALUES
                        (:server_id, :last_seen, :last_scanned)
                    """
                ),
                {
                    "server_id": server_id,
                    "last_seen": now,
                    "last_scanned": now,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, model_version, scored_at)
                    VALUES
                        (:server_id, :axis_name, :model_version, :scored_at)
                    """
                ),
                {
                    "server_id": server_id,
                    "axis_name": "security",
                    "model_version": "v1",
                    "scored_at": now - age,
                },
            )

    from sqlalchemy.orm import Session

    session = Session(engine)
    test_app = _create_test_app()

    def override_get_session():
        yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/scores/staleness-lag")
            assert response.status_code == 200, response.text
            payload = response.json()

        assert len(payload) == 1
        result = payload[0]
        assert result["axis_name"] == "security"
        counts = {item["bucket"]: item["count"] for item in result["buckets"]}
        assert counts == {
            "lt_1h": 1,
            "lt_1d": 0,
            "lt_7d": 1,
            "lt_30d": 0,
            "gt_30d": 1,
        }
        assert result["stale_total"] == 1
        assert result["oldest_scored_at"] is not None
    finally:
        session.close()
        engine.dispose()


def main() -> None:
    _run_self_test()
    print("PASS")


if __name__ == "__main__":
    main()