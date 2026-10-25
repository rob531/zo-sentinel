from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_escalated_to_distribution


router = APIRouter(prefix="/api")


class EscalatedToDistributionQuery(BaseModel):
    since_days: int = Field(default=30, ge=1, le=365)


class EscalatedToCount(BaseModel):
    escalated_to: str | None
    count: int


class AxisEscalatedToDistribution(BaseModel):
    axis_name: str
    total: int
    by_target: list[EscalatedToCount]


class EscalatedToDistributionResponse(BaseModel):
    since_days: int
    axes: list[AxisEscalatedToDistribution]


def _normalize_axes(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        axes = result.get("axes")
        if axes is None:
            axes = [
                {"axis_name": axis_name, **axis}
                for axis_name, axis in result.items()
                if isinstance(axis, dict)
            ]
    else:
        axes = result

    normalized: list[dict[str, Any]] = []
    for axis in axes:
        by_target = axis.get("by_target", {})
        if isinstance(by_target, dict):
            by_target = [
                {"escalated_to": target, "count": count}
                for target, count in by_target.items()
            ]

        normalized.append(
            {
                "axis_name": axis["axis_name"],
                "total": axis.get(
                    "total",
                    sum(item["count"] for item in by_target),
                ),
                "by_target": by_target,
            }
        )

    return normalized


@router.get(
    "/axes/escalated-to/distribution",
    response_model=EscalatedToDistributionResponse,
)
def get_axis_escalated_to_distribution_endpoint(
    query: EscalatedToDistributionQuery = Depends(),
    session: Session = Depends(get_session),
) -> EscalatedToDistributionResponse:
    result = get_axis_escalated_to_distribution(session, query.since_days)
    return EscalatedToDistributionResponse(
        since_days=query.since_days,
        axes=_normalize_axes(result),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    axis_name VARCHAR NOT NULL,
                    escalated_to VARCHAR,
                    escalated BOOLEAN NOT NULL,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, escalated_to, escalated, scored_at)
                VALUES
                    (:axis_name, :escalated_to, :escalated, :scored_at)
                """
            ),
            [
                {
                    "axis_name": "axis-a",
                    "escalated_to": "target-a",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-a",
                    "escalated_to": "target-b",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-b",
                    "escalated_to": "target-c",
                    "escalated": False,
                    "scored_at": now,
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/axes/escalated-to/distribution",
        params={"since_days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["since_days"] == 30
    assert all(axis["axis_name"] != "axis-b" for axis in payload["axes"])
    axis_a = next(axis for axis in payload["axes"] if axis["axis_name"] == "axis-a")
    assert axis_a["total"] == 2
    assert {item["escalated_to"]: item["count"] for item in axis_a["by_target"]} == {
        "target-a": 1,
        "target-b": 1,
    }
    assert sum(axis["total"] for axis in payload["axes"]) == 2
    print("PASS")