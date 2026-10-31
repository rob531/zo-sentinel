from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_run_duration_trend as _get_run_duration_trend

router = APIRouter(tags=["cadence"])


@router.get("/run-duration-trend")
async def run_duration_trend(
    job: str = Query(..., min_length=1, description="Scanning cadence job name"),
    days: int = Query(30, ge=1, le=365, description="Number of recent days to include"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _get_run_duration_trend(session=session, job=job, days=days)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()

    async def _override_get_session():
        yield None

    that_app.dependency_overrides[get_session] = _override_get_session
    that_app.include_router(router, prefix="/api/cadence")

    route_paths = {route.path for route in that_app.routes}
    assert "/api/cadence/run-duration-trend" in route_paths
    print("PASS")