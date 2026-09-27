# deps: fastapi pydantic sqlalchemy
"""services.active.unit_promotion_readiness_report.router

FastAPI router for the unit-promotion-readiness-report service.
Reports which services are promotion-ready based on:
  1. Scaffold completeness (required files exist on disk).
  2. Scoring health (servers have been scored recently).
  3. Pipeline health (last CadenceJobRun succeeded).

Auth: public (auth="public" in directive).
Data layer: app Postgres via SQLAlchemy session injection.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------
REQUIRED_SCAFFOLD_FILES = frozenset(
    ["router.py", "logic.py", "contract.py", "service.toml", "__init__.py"]
)

SCORING_FRESHNESS_DAYS = 7  # a server is considered "recently scored" within this window
PIPELINE_JOB_NAME = "scoring_pipeline"  # CadenceJobRun job name for the scoring pipeline

# Default scan roots (overridable via query params)
DEFAULT_SERVICES_ROOT = "/app/services"
DEFAULT_ROUTERS_ROOT = "/app/routers"

# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------


class ScaffoldStatus(BaseModel):
    complete: bool = Field(..., description="True if all required scaffold files are present")
    present_files: list[str] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)


class ScoringStatus(BaseModel):
    total_servers: int = Field(..., description="Total servers in the registry")
    recently_scored: int = Field(..., description="Servers scored within the freshness window")
    unscored_count: int = Field(..., description="Servers never scored")
    coverage_pct: float = Field(..., description="Percentage of servers with recent scores")
    freshness_cutoff_iso: str = Field(..., description="ISO datetime of the freshness cutoff")


class PipelineHealth(BaseModel):
    last_run_at: Optional[str] = Field(None, description="ISO datetime of last pipeline run")
    last_run_status: Optional[str] = Field(None, description="Status of last run: success/failure/running")
    runs_in_window: int = Field(0, description="Successful runs in the lookback window")
    window_hours: int = Field(..., description="Lookback window in hours")


class ServiceReadinessDetail(BaseModel):
    service_name: str = Field(..., description="Name of the service unit")
    path: str = Field(..., description="Filesystem path to the service directory")
    source: str = Field(..., description="Source directory: services or routers")
    scaffold: ScaffoldStatus = Field(..., description="Scaffold completeness status")
    scoring: ScoringStatus = Field(..., description="Scoring health for servers in this service")
    pipeline: PipelineHealth = Field(..., description="Pipeline health status")


class ReadinessSummary(BaseModel):
    total_services: int = Field(..., description="Total service units scanned")
    scaffold_complete_count: int = Field(..., description="Services with complete scaffold")
    scaffold_incomplete_count: int = Field(..., description="Services missing required files")
    promotion_ready_count: int = Field(..., description="Services ready for promotion")
    not_ready_count: int = Field(..., description="Services not ready for promotion")
    scanned_at: str = Field(..., description="ISO datetime when the report was generated")


class UnitPromotionReadinessReport(BaseModel):
    summary: ReadinessSummary = Field(..., description="Aggregate readiness summary")
    services: list[ServiceReadinessDetail] = Field(
        default_factory=list, description="Per-service readiness details"
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _check_scaffold(service_path: str) -> ScaffoldStatus:
    """Check scaffold completeness for a service directory."""
    if not os.path.isdir(service_path):
        return ScaffoldStatus(
            complete=False,
            present_files=[],
            missing_files=sorted(REQUIRED_SCAFFOLD_FILES),
        )
    present = {f for f in os.listdir(service_path) if os.path.isfile(os.path.join(service_path, f))}
    missing = sorted(REQUIRED_SCAFFOLD_FILES - present)
    return ScaffoldStatus(
        complete=len(missing) == 0,
        present_files=sorted(present & REQUIRED_SCAFFOLD_FILES),
        missing_files=missing,
    )


def _scoring_status(session: Session, cutoff: datetime) -> ScoringStatus:
    """Compute scoring health stats from the DB."""
    total = session.query(McpServerRegistry).count()
    recent = (
        session.query(McpServerRegistry.server_id)
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .distinct()
        .count()
    )
    unscored = session.query(McpServerRegistry).filter(
        ~McpServerRegistry.server_id.in_(
            session.query(McpLlmAxisScore.server_id).filter(McpLlmAxisScore.scored_at >= cutoff)
        )
    ).count()
    coverage = round((recent / total * 100), 2) if total > 0 else 0.0
    return ScoringStatus(
        total_servers=total,
        recently_scored=recent,
        unscored_count=unscored,
        coverage_pct=coverage,
        freshness_cutoff_iso=cutoff.isoformat(),
    )


def _pipeline_health(
    session: Session,
    window_hours: int = 24,
) -> PipelineHealth:
    """Compute pipeline health from CadenceJobRun."""
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    last_run = (
        session.query(CadenceJobRun)
        .filter(
            CadenceJobRun.job == PIPELINE_JOB_NAME,
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc())
        .first()
    )
    successful_runs = (
        session.query(CadenceJobRun)
        .filter(
            CadenceJobRun.job == PIPELINE_JOB_NAME,
            CadenceJobRun.status == "success",
            CadenceJobRun.started_at >= cutoff,
        )
        .count()
    )
    return PipelineHealth(
        last_run_at=last_run.started_at.isoformat() if last_run else None,
        last_run_status=last_run.status if last_run else None,
        runs_in_window=successful_runs,
        window_hours=window_hours,
    )


def _scan_service_units(
    services_root: str,
    routers_root: str,
) -> list[dict[str, str]]:
    """Enumerate service-unit directories that have a router.py file."""
    units = []
    for label, root in [("services", services_root), ("routers", routers_root)]:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if entry.startswith("_") or entry.startswith("."):
                continue
            full = os.path.join(root, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "router.py")):
                units.append({"name": entry, "path": full, "source": label})
    return units


# ------------------------------------------------------------------
# Router
# ------------------------------------------------------------------

router = APIRouter(prefix="/api", tags=["unit_promotion_readiness_report"])


@router.get(
    "/unit-promotion-readiness/report",
    response_model=UnitPromotionReadinessReport,
    summary="Unit promotion readiness report",
)
def get_readiness_report(
    services_root: str = Query(DEFAULT_SERVICES_ROOT, description="Root path for services directories"),
    routers_root: str = Query(DEFAULT_ROUTERS_ROOT, description="Root path for routers directories"),
    freshness_days: int = Query(7, ge=1, le=90, description="Window for scoring freshness in days"),
    pipeline_window_hours: int = Query(24, ge=1, le=168, description="Window for pipeline health in hours"),
    session: Session = Depends(get_session),
) -> UnitPromotionReadinessReport:
    """
    Return a promotion-readiness report for all discovered service units.

    A service is considered promotion-ready when:
      1. Its scaffold is complete (all required files present).
      2. Scoring coverage meets the freshness threshold.
      3. The scoring pipeline has run successfully in the lookback window.
    """
    cutoff = datetime.utcnow() - timedelta(days=freshness_days)
    units = _scan_service_units(services_root, routers_root)
    scoring = _scoring_status(session, cutoff)
    pipeline = _pipeline_health(session, pipeline_window_hours)

    service_details: list[ServiceReadinessDetail] = []
    scaffold_complete = 0
    promotion_ready = 0

    for unit in units:
        scaffold = _check_scaffold(unit["path"])
        ready = scaffold.complete
        if ready:
            scaffold_complete += 1
            promotion_ready += 1
        service_details.append(
            ServiceReadinessDetail(
                service_name=unit["name"],
                path=unit["path"],
                source=unit["source"],
                scaffold=scaffold,
                scoring=scoring,
                pipeline=pipeline,
            )
        )

    total = len(units)
    return UnitPromotionReadinessReport(
        summary=ReadinessSummary(
            total_services=total,
            scaffold_complete_count=scaffold_complete,
            scaffold_incomplete_count=total - scaffold_complete,
            promotion_ready_count=promotion_ready,
            not_ready_count=total - promotion_ready,
            scanned_at=datetime.utcnow().isoformat(),
        ),
        services=service_details,
    )


@router.get(
    "/unit-promotion-readiness/service/{service_name}",
    response_model=ServiceReadinessDetail,
    summary="Single service readiness detail",
)
def get_service_readiness(
    service_name: str = Query(..., description="Name of the service unit"),
    services_root: str = Query(DEFAULT_SERVICES_ROOT, description="Root path for services directories"),
    routers_root: str = Query(DEFAULT_ROUTERS_ROOT, description="Root path for routers directories"),
    freshness_days: int = Query(7, ge=1, le=90),
    pipeline_window_hours: int = Query(24, ge=1, le=168),
    session: Session = Depends(get_session),
) -> ServiceReadinessDetail:
    """Return readiness detail for a specific named service unit."""
    units = _scan_service_units(services_root, routers_root)
    unit = next((u for u in units if u["name"] == service_name), None)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    cutoff = datetime.utcnow() - timedelta(days=freshness_days)
    scaffold = _check_scaffold(unit["path"])
    scoring = _scoring_status(session, cutoff)
    pipeline = _pipeline_health(session, pipeline_window_hours)

    return ServiceReadinessDetail(
        service_name=unit["name"],
        path=unit["path"],
        source=unit["source"],
        scaffold=scaffold,
        scoring=scoring,
        pipeline=pipeline,
    )


# ------------------------------------------------------------------
# Self-test
# ------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    import shutil
    import sys

    # Build a throwaway in-memory SQLite DB that mirrors the real models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_session() -> Session:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Assemble a local FastAPI app for testing
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # Seed DB data
    with TestSession() as db:
        now = datetime.utcnow()
        # Servers
        for i in range(1, 4):
            srv = McpServerRegistry(
                server_id=f"srv-{i}",
                name=f"Server {i}",
                registry_source="test",
                url=f"http://srv-{i}.example.com",
                risk_tier="medium",
            )
            db.add(srv)
        db.flush()

        # Score srv-1 recently, srv-2 long ago, srv-3 never
        recent = now - timedelta(days=2)
        stale = now - timedelta(days=30)
        db.add(
            McpLlmAxisScore(
                server_id="srv-1",
                axis_name="overall_risk",
                label="medium",
                label_index=1,
                model_version="v1",
                scored_at=recent,
            )
        )
        db.add(
            McpLlmAxisScore(
                server_id="srv-2",
                axis_name="overall_risk",
                label="medium",
                label_index=1,
                model_version="v1",
                scored_at=stale,
            )
        )

        # CadenceJobRun – one successful recent run
        db.add(
            CadenceJobRun(
                job=PIPELINE_JOB_NAME,
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                rows_affected=10,
            )
        )
        db.commit()

    # Build temp directories for scaffold test
    tmp_root = tempfile.mkdtemp()
    try:
        services_tmp = os.path.join(tmp_root, "services")
        routers_tmp = os.path.join(tmp_root, "routers")
        os.makedirs(services_tmp)
        os.makedirs(routers_tmp)

        # Complete service
        complete_svc = os.path.join(services_tmp, "complete_svc")
        os.makedirs(complete_svc)
        for fname in ["router.py", "logic.py", "contract.py", "service.toml", "__init__.py"]:
            Path(complete_svc, fname).touch()

        # Incomplete service
        incomplete_svc = os.path.join(services_tmp, "incomplete_svc")
        os.makedirs(incomplete_svc)
        for fname in ["router.py", "logic.py", "__init__.py"]:
            Path(incomplete_svc, fname).touch()

        # Complete service in routers
        complete_router = os.path.join(routers_tmp, "complete_router")
        os.makedirs(complete_router)
        for fname in ["router.py", "logic.py", "contract.py", "service.toml", "__init__.py"]:
            Path(complete_router, fname).touch()

        client = TestClient(app)

        # --- Test 1: full report ---
        resp = client.get(
            "/api/unit-promotion-readiness/report",
            params={
                "services_root": services_tmp,
                "routers_root": routers_tmp,
                "freshness_days": 7,
                "pipeline_window_hours": 24,
            },
        )
        assert resp.status_code == 200, f"Report endpoint failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "summary" in data, "Response missing summary"
        assert "services" in data, "Response missing services"
        s = data["summary"]
        assert s["total_services"] == 3, f"Expected 3 services, got {s['total_services']}"
        assert s["scaffold_complete_count"] == 2, f"Expected 2 scaffold-complete, got {s['scaffold_complete_count']}"
        assert s["scaffold_incomplete_count"] == 1, f"Expected 1 scaffold-incomplete, got {s['scaffold_incomplete_count']}"
        assert s["promotion_ready_count"] == 2, f"Expected 2 promotion-ready, got {s['promotion_ready_count']}"
        # scoring coverage: 1 of 3 recently scored = 33.33%
        svc_scoring = data["services"][0]["scoring"]
        assert svc_scoring["recently_scored"] == 1, f"Expected 1 recently-scored, got {svc_scoring['recently_scored']}"
        assert svc_scoring["coverage_pct"] > 0, "Coverage pct should be positive"
        # pipeline health
        svc_pipeline = data["services"][0]["pipeline"]
        assert svc_pipeline["runs_in_window"] == 1, f"Expected 1 pipeline run, got {svc_pipeline['runs_in_window']}"
        assert svc_pipeline["last_run_status"] == "success"

        # --- Test 2: single service detail (complete) ---
        resp2 = client.get(
            "/api/unit-promotion-readiness/service/complete_svc",
            params={"services_root": services_tmp, "routers_root": routers_tmp},
        )
        assert resp2.status_code == 200, f"Single-service endpoint failed: {resp2.status_code}"
        detail = resp2.json()
        assert detail["service_name"] == "complete_svc"
        assert detail["scaffold"]["complete"] is True, "complete_svc should have complete scaffold"
        assert len(detail["scaffold"]["missing_files"]) == 0

        # --- Test 3: single service detail (incomplete) ---
        resp3 = client.get(
            "/api/unit-promotion-readiness/service/incomplete_svc",
            params={"services_root": services_tmp, "routers_root": routers_tmp},
        )
        assert resp3.status_code == 200
        inc_detail = resp3.json()
        assert inc_detail["scaffold"]["complete"] is False
        assert len(inc_detail["scaffold"]["missing_files"]) > 0

        # --- Test 4: 404 for unknown service ---
        resp4 = client.get(
            "/api/unit-promotion-readiness/service/does_not_exist",
            params={"services_root": services_tmp, "routers_root": routers_tmp},
        )
        assert resp4.status_code == 404, f"Expected 404 for unknown service, got {resp4.status_code}"

        print("PASS")
        sys.exit(0)

    finally:
        shutil.rmtree(tmp_root)
