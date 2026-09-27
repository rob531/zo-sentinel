# deps: fastapi, sqlalchemy, pydantic, requests
"""FastAPI router for daemon roster coverage report.
Provides an endpoint that returns coverage information for each MCP server
based on the presence of LLM axis scores.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import select, func

# Import the shared DB session and ORM models from the application.
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

# Total number of distinct axes defined for LLM scores.
TOTAL_AXES = 7


class ServerCoverage(BaseModel):
    server_id: int = Field(..., description="Primary key of the server")
    name: str = Field(..., description="Human‑readable name of the server")
    coverage_percent: float = Field(..., ge=0, le=100, description="Percentage of LLM axes for which a score exists")


@router.get("/daemon_roster_coverage_report", response_model=List[ServerCoverage])
async def daemon_roster_coverage_report(db: Session = Depends(get_session)):
    """Return coverage percentages for all registered MCP servers.

    For each server we count how many distinct ``axis_name`` rows exist in
    ``McpLlmAxisScore`` for the latest ``model_version`` and compute the
    percentage over the total number of axes (7).
    """
    # Sub‑query: latest model_version per server.
    sub_latest = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.model_version).label("max_version"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join to obtain distinct axis counts for the latest version.
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_cnt"),
        )
        .select_from(McpServerRegistry)
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .join(sub_latest, (McpLlmAxisScore.server_id == sub_latest.c.server_id) & (McpLlmAxisScore.model_version == sub_latest.c.max_version))
        .group_by(McpServerRegistry.server_id, McpServerRegistry.name)
    )

    results = db.execute(stmt).all()
    coverage_list: List[ServerCoverage] = []
    for server_id, name, axis_cnt in results:
        percent = (axis_cnt / TOTAL_AXES) * 100.0
        coverage_list.append(ServerCoverage(server_id=server_id, name=name, coverage_percent=percent))
    return coverage_list


if __name__ == "__main__":
    # Simple self‑test: import the router and ensure the endpoint is registered.
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    # Count routes that belong to this router (exclude automatic docs routes).
    route_paths = [r.path for r in app.routes]
    expected_path = "/api/daemon_roster_coverage_report"
    if expected_path not in route_paths:
        print(f"FAIL: expected route {expected_path} not found")
    else:
        print("PASS")
