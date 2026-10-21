from collections import Counter
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Iterator, List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


LEGAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisPercentileBin(BaseModel):
    label: str
    count: int


class AxisPercentileResponse(BaseModel):
    axis: str
    days: int
    count: int
    min: float
    p25: float
    median: float
    p75: float
    max: float
    bins: List[AxisPercentileBin]


router = APIRouter(prefix="/api")


def _percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _as_utc(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get(
    "/scores/axis-percentiles",
    response_model=AxisPercentileResponse,
)
def axis_percentile_distribution(
    axis: str,
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> AxisPercentileResponse:
    if axis not in LEGAL_AXES:
        raise HTTPException(status_code=400, detail="Unknown axis")

    statement = (
        text(
            """
            SELECT s.server_id, s.axis_name, s.label, s.p_top, s.scored_at
            FROM mcp_llm_axis_scores AS s
            INNER JOIN mcp_server_registry AS r
                ON r.server_id = s.server_id
            WHERE s.axis_name = :axis
            """
        )
        .bindparams(axis=axis)
    )
    rows = session.execute(statement).fetchall()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    values = []
    labels = Counter()

    for server_id, row_axis, label, p_top, scored_at in rows:
        if scored_at is None:
            continue
        try:
            if _as_utc(scored_at) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        if p_top is None:
            continue
        values.append(float(p_top))
        bin_label = label if label in LEGAL_AXES else axis
        labels[bin_label] += 1

    values.sort()
    count = len(values)
    bins = [
        AxisPercentileBin(label=label, count=labels.get(label, 0))
        for label in LEGAL_AXES
    ]

    return AxisPercentileResponse(
        axis=axis,
        days=days,
        count=count,
        min=min(values) if values else 0.0,
        p25=_percentile(values, 0.25),
        median=_percentile(values, 0.50),
        p75=_percentile(values, 0.75),
        max=max(values) if values else 0.0,
        bins=bins,
    )


def run() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        risk_tier VARCHAR,
                        registry_source VARCHAR
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE mcp_llm_axis_scores (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id VARCHAR,
                        axis_name VARCHAR,
                        label VARCHAR,
                        p_top FLOAT,
                        scored_at DATETIME
                    )
                    """
                )
            )

            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, registry_source)
                    VALUES
                        (:server_id, :name, :risk_tier, :registry_source)
                    """
                ),
                [
                    {
                        "server_id": "contract-server-1",
                        "name": "Contract Server 1",
                        "risk_tier": "high",
                        "registry_source": "contract",
                    },
                    {
                        "server_id": "contract-server-2",
                        "name": "Contract Server 2",
                        "risk_tier": "medium",
                        "registry_source": "contract",
                    },
                    {
                        "server_id": "contract-server-3",
                        "name": "Contract Server 3",
                        "risk_tier": "low",
                        "registry_source": "contract",
                    },
                ],
            )

            now = datetime.now(timezone.utc)
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, label, p_top, scored_at)
                    VALUES
                        (:server_id, :axis_name, :label, :p_top, :scored_at)
                    """
                ),
                [
                    {
                        "server_id": "contract-server-1",
                        "axis_name": "overall_risk",
                        "label": "overall_risk",
                        "p_top": 0.90,
                        "scored_at": now.isoformat(),
                    },
                    {
                        "server_id": "contract-server-2",
                        "axis_name": "overall_risk",
                        "label": "overall_risk",
                        "p_top": 0.50,
                        "scored_at": (now - timedelta(minutes=1)).isoformat(),
                    },
                    {
                        "server_id": "contract-server-3",
                        "axis_name": "auth_strength",
                        "label": "auth_strength",
                        "p_top": 0.70,
                        "scored_at": (now - timedelta(minutes=2)).isoformat(),
                    },
                ],
            )

        def override_get_session() -> Iterator[Session]:
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get(
                "/api/scores/axis-percentiles",
                params={"axis": "overall_risk", "days": 30},
            )
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["axis"] == "overall_risk"
            assert payload["days"] == 30
            assert payload["count"] == 2
            assert payload["min"] == 0.50
            assert payload["max"] == 0.90
            assert 0.50 <= payload["median"] <= 0.90
            assert len(payload["bins"]) == len(LEGAL_AXES)
            assert next(
                item for item in payload["bins"] if item["label"] == "overall_risk"
            )["count"] == 2

            response = client.get(
                "/api/scores/axis-percentiles",
                params={"axis": "auth_strength", "days": 30},
            )
            assert response.status_code == 200, response.text
            assert response.json()["count"] == 1

            response = client.get(
                "/api/scores/axis-percentiles",
                params={"axis": "not_a_real_axis", "days": 30},
            )
            assert response.status_code == 400, response.text
    finally:
        engine.dispose()

    print("PASS")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())