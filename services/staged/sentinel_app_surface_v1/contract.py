"""
services/staged/sentinel_app_surface_v1/contract.py

Self‑contained FastAPI contract for the *sentinel_app_surface_v1* service.
It defines the public response schemas for the four API units and provides
a lightweight in‑process test that validates the contract without any
external dependencies (no network, no real database).

Running the module as a script executes the self‑test and prints ``PASS``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from fastapi import APIRouter, FastAPI, Query
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
#                           Response Schemas                                 #
# --------------------------------------------------------------------------- #

class DashboardSummaryResponse(BaseModel):
    total_servers: int
    tier_distribution: Dict[str, int]
    avg_composite: float
    new_today: int
    assessed_count: int
    last_updated: _dt.datetime


class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]


class VerdictResponse(BaseModel):
    server_id: str
    axes: List[AxisScore]
    overall: float = Field(..., alias="p_top")
    risk_tier: str
    criteria_version: str
    overridden: bool


class EntityHit(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    registry_source: str
    composite_score: float
    last_assessed: _dt.datetime


class EntitySearchResponse(BaseModel):
    results: List[EntityHit]
    total: int
    page: int
    page_size: int


class ServerMeta(BaseModel):
    server_id: str
    name: str
    url: str
    description: str


class EntityReportResponse(BaseModel):
    server: ServerMeta
    axes: List[AxisScore]
    threat_count: int
    attestation_count: int
    generated_at: _dt.datetime


# --------------------------------------------------------------------------- #
#                               Routers                                      #
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/api")


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    name="Dashboard Summary",
)
def get_dashboard_summary() -> DashboardSummaryResponse:
    """Return a static but schema‑valid dashboard summary."""
    return DashboardSummaryResponse(
        total_servers=3,
        tier_distribution={"HIGH_RISK_ISOLATED": 1, "MEDIUM_RISK": 2},
        avg_composite=0.73,
        new_today=0,
        assessed_count=3,
        last_updated=_dt.datetime.utcnow(),
    )


@router.get(
    "/servers/{server_id}/verdict",
    response_model=VerdictResponse,
    name="Server Verdict",
)
def get_server_verdict(server_id: str) -> VerdictResponse:
    """Return a static verdict for the requested server."""
    axes = [
        AxisScore(
            axis_name="confidentiality",
            label="Low",
            label_index=0,
            p_top=0.85,
            p_critical=0.10,
            p_danger=0.05,
            probs=[0.85, 0.10, 0.05],
        ),
        AxisScore(
            axis_name="integrity",
            label="Medium",
            label_index=1,
            p_top=0.60,
            p_critical=0.30,
            p_danger=0.10,
            probs=[0.60, 0.30, 0.10],
        ),
    ]
    overall = sum(a.p_top for a in axes) / len(axes)
    return VerdictResponse(
        server_id=server_id,
        axes=axes,
        p_top=overall,
        risk_tier="MEDIUM_RISK",
        criteria_version="v1.0",
        overridden=False,
    )


@router.get(
    "/search/entities",
    response_model=EntitySearchResponse,
    name="Entity Search",
)
def search_entities(
    q: str = Query("", description="Free‑text query"),
    tier: str | None = Query(None, description="Risk tier filter"),
    source: str | None = Query(None, description="Registry source filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> EntitySearchResponse:
    """Return a static paginated list of entity hits."""
    # In a real implementation the query parameters would be honoured.
    hits = [
        EntityHit(
            server_id="srv-001",
            name="Alpha",
            risk_tier="HIGH_RISK_ISOLATED",
            registry_source="source‑a",
            composite_score=0.92,
            last_assessed=_dt.datetime.utcnow(),
        ),
        EntityHit(
            server_id="srv-002",
            name="Beta",
            risk_tier="MEDIUM_RISK",
            registry_source="source‑b",
            composite_score=0.68,
            last_assessed=_dt.datetime.utcnow(),
        ),
    ]
    total = len(hits)
    start = (page - 1) * page_size
    end = start + page_size
    return EntitySearchResponse(
        results=hits[start:end],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/servers/{server_id}/report",
    response_model=EntityReportResponse,
    name="Server Report",
)
def get_server_report(server_id: str) -> EntityReportResponse:
    """Return a static deep report for the requested server."""
    axes = [
        AxisScore(
            axis_name="availability",
            label="High",
            label_index=2,
            p_top=0.95,
            p_critical=0.02,
            p_danger=0.03,
            probs=[0.95, 0.02, 0.03],
        ),
        AxisScore(
            axis_name="confidentiality",
            label="Low",
            label_index=0,
            p_top=0.80,
            p_critical=0.15,
            p_danger=0.05,
            probs=[0.80, 0.15, 0.05],
        ),
        AxisScore(
            axis_name="integrity",
            label="Medium",
            label_index=1,
            p_top=0.70,
            p_critical=0.20,
            p_danger=0.10,
            probs=[0.70, 0.20, 0.10],
        ),
    ]
    meta = ServerMeta(
        server_id=server_id,
        name="Demo Server",
        url="https://example.com",
        description="A placeholder server used for contract self‑test.",
    )
    return EntityReportResponse(
        server=meta,
        axes=axes,
        threat_count=2,
        attestation_count=5,
        generated_at=_dt.datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
#                               Application                                   #
# --------------------------------------------------------------------------- #

def create_app() -> FastAPI:
    """Factory that builds the FastAPI application for this contract."""
    app = FastAPI(title="sentinel_app_surface_v1 contract")
    app.include_router(router)
    return app


app = create_app()


# --------------------------------------------------------------------------- #
#                               Self‑test                                      #
# --------------------------------------------------------------------------- #

def _run_self_test() -> None:
    """Execute a minimal contract verification using FastAPI's TestClient."""
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # 1. Dashboard summary
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200, "Dashboard summary endpoint failed"
    data = resp.json()
    for key in [
        "total_servers",
        "tier_distribution",
        "avg_composite",
        "new_today",
        "assessed_count",
        "last_updated",
    ]:
        assert key in data, f"Missing key {key} in dashboard summary"

    # 2. Server verdict
    resp = client.get("/api/servers/srv-001/verdict")
    assert resp.status_code == 200, "Server verdict endpoint failed"
    data = resp.json()
    for key in ["server_id", "axes", "p_top", "risk_tier", "criteria_version", "overridden"]:
        assert key in data, f"Missing key {key} in verdict response"

    # 3. Entity search
    resp = client.get("/api/search/entities?q=test&page=1&page_size=10")
    assert resp.status_code == 200, "Entity search endpoint failed"
    data = resp.json()
    for key in ["results", "total", "page", "page_size"]:
        assert key in data, f"Missing key {key} in search response"

    # 4. Server report
    resp = client.get("/api/servers/srv-001/report")
    assert resp.status_code == 200, "Server report endpoint failed"
    data = resp.json()
    for key in ["server", "axes", "threat_count", "attestation_count", "generated_at"]:
        assert key in data, f"Missing key {key} in report response"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()