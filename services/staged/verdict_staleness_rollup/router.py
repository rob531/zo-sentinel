from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_verdict_staleness_rollup


router = APIRouter()


class VerdictStalenessResponse(BaseModel):
    buckets: dict[str, int]
    by_verdict: dict[str, int]
    by_risk_tier: dict[str, int]
    oldest_unassessed: list[dict[str, Any]]
    totals: dict[str, Any]


@router.get(
    "/registry/verdict-staleness",
    response_model=VerdictStalenessResponse,
    summary="Verdict assessment staleness rollup",
)
def verdict_staleness(
    session: Session = Depends(get_session),
) -> VerdictStalenessResponse:
    result = get_verdict_staleness_rollup(session)
    return VerdictStalenessResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: None

    route = next(
        route
        for route in test_app.routes
        if getattr(route, "path", None) == "/api/registry/verdict-staleness"
    )
    assert route is not None
    print("PASS")