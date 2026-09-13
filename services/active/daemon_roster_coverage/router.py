# deps: fastapi, sqlalchemy, pydantic
"""FastAPI router for daemon roster coverage report."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

TOTAL_AXES = 7


class ServerCoverage(BaseModel):
    server_id: int = Field(..., description="Primary key of the server")
    name: str = Field(..., description="Human-readable name of the server")
    coverage_percent: float = Field(..., ge=0, le=100, description="Percentage of LLM axes for which a score exists")


@router.get("/daemon_roster_coverage_report", response_model=List[ServerCoverage])
async def daemon_roster_coverage_report(db: Session = Depends(get_session)):
    sub_latest = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.model_version).label("max_version"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_cnt"),
        )
        .select_from(McpServerRegistry)
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .join(
            sub_latest,
            (McpLlmAxisScore.server_id == sub_latest.c.server_id)
            & (McpLlmAxisScore.model_version == sub_latest.c.max_version),
        )
        .group_by(McpServerRegistry.server_id, McpServerRegistry.name)
    )
    results = db.execute(stmt).all()
    coverage_list: List[ServerCoverage] = []
    for server_id, name, axis_cnt in results:
        percent = (axis_cnt / TOTAL_AXES) * 100.0
        coverage_list.append(
            ServerCoverage(server_id=server_id, name=name, coverage_percent=percent)
        )
    return coverage_list


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from app.main import app as main_app

    test_app = FastAPI()
    test_app.include_router(router)

    _override = main_app.dependency_overrides.get(get_session)
    if _override:
        test_app.dependency_overrides[get_session] = _override

    route_paths = [r.path for r in test_app.routes]
    expected_path = "/api/daemon_roster_coverage_report"
    if expected_path not in route_paths:
        print("FAIL")
    else:
        print("PASS")
