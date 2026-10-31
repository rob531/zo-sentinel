from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_regression

router = APIRouter(prefix="/api")


class VerdictRegressionBucket(BaseModel):
    date: str
    total: int
    verdicts: dict[str, int]
    risk_tiers: dict[str, int]


class VerdictRegressionResponse(BaseModel):
    days: int
    from_date: str
    to_date: str
    buckets: list[VerdictRegressionBucket]
    deltas: dict[str, int]
    total_scored: int


RegistryVerdictRegressionResponse = VerdictRegressionResponse


@router.get(
    "/registry/verdict-regression",
    response_model=VerdictRegressionResponse,
    summary="Registry verdict and risk-tier regression",
)
async def registry_verdict_regression(
    days: int = Query(default=30, ge=1, le=365),
    session: Any = Depends(get_session),
) -> VerdictRegressionResponse:
    result: (
        VerdictRegressionResponse
        | dict[str, Any]
        | Awaitable[VerdictRegressionResponse | dict[str, Any]]
    ) = get_verdict_regression(session, days)

    if isinstance(result, Awaitable):
        result = await result

    if isinstance(result, VerdictRegressionResponse):
        return result
    return VerdictRegressionResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    local_app = FastAPI()
    local_app.include_router(router)

    async def fake_get_verdict_regression(
        session: Any,
        days: int,
    ) -> dict[str, Any]:
        return {
            "days": days,
            "from_date": "2025-01-01",
            "to_date": "2025-01-30",
            "buckets": [
                {
                    "date": "2025-01-01",
                    "total": 3,
                    "verdicts": {"SAFE": 1, "HIGH_RISK_ISOLATED": 2},
                    "risk_tiers": {"LOW": 1, "HIGH": 2},
                },
                {
                    "date": "2025-01-02",
                    "total": 3,
                    "verdicts": {"SAFE": 2, "HIGH_RISK_ISOLATED": 1},
                    "risk_tiers": {"LOW": 2, "HIGH": 1},
                },
            ],
            "deltas": {"LOW": 1, "HIGH": -1},
            "total_scored": 6,
        }

    original_get_verdict_regression = get_verdict_regression
    get_verdict_regression = fake_get_verdict_regression
    local_app.dependency_overrides[get_session] = lambda: object()

    try:
        with TestClient(local_app) as client:
            response = client.get(
                "/api/registry/verdict-regression",
                params={"days": 30},
            )
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["buckets"]) >= 1
            assert payload["buckets"][0]["verdicts"] == {
                "SAFE": 1,
                "HIGH_RISK_ISOLATED": 2,
            }
            assert "HIGH" in payload["deltas"]
    finally:
        get_verdict_regression = original_get_verdict_regression

    print("PASS")