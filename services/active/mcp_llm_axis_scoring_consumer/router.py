# deps: fastapi, pydantic, sqlalchemy, requests
"""
MCP LLM Axis Scoring Consumer.

Reads axis scores (mcp_llm_axis_scores) for each server, computes an overall
risk score from the 7-axis p_top/p_critical/p_danger distributions, assigns a
risk_tier, and writes the result back to mcp_server_registry.risk_tier.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on McpLlmAxisScore / McpServerRegistry.
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_URL = f"{WRITE_SERVICE_URL}/service_health"
REQUEST_TIMEOUT = 10
HEARTBEAT_INTERVAL = 60  # seconds

ALL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

# Risk tier thresholds (based on composite p_top)
TIER_THRESHOLDS = {
    "low": 0.20,
    "medium": 0.45,
    "high": 0.70,
    # > high -> critical
}

router = APIRouter(prefix="/api", tags=["mcp_llm_axis_scoring_consumer"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _composite_risk(
    p_top: Optional[float],
    p_critical: Optional[float],
    p_danger: Optional[float],
) -> float:
    """Weighted composite from the three probabilities."""
    if p_top is None and p_critical is None and p_danger is None:
        return 0.0
    t = p_top or 0.0
    c = p_critical or 0.0
    d = p_danger or 0.0
    return round(0.50 * t + 0.30 * c + 0.20 * d, 6)


def _assign_tier(composite: float) -> str:
    if composite < TIER_THRESHOLDS["low"]:
        return "low"
    if composite < TIER_THRESHOLDS["medium"]:
        return "medium"
    if composite < TIER_THRESHOLDS["high"]:
        return "high"
    return "critical"


def _send_heartbeat() -> bool:
    try:
        resp = requests.post(
            SERVICE_HEALTH_URL,
            json={
                "service": "mcp_llm_axis_scoring_consumer",
                "status": "running",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code in (200, 201, 202)
    except requests.RequestException as exc:
        logger.warning("Heartbeat failed: %s", exc)
        return False


@contextmanager
def _db_session() -> Iterator[Session]:
    """Context manager yielding a single get_session cycle."""
    gen = get_session()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisScoreOut(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    composite: float
    escalated: Optional[bool]
    model_version: str
    scored_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ServerScoringResult(BaseModel):
    server_id: str
    server_name: Optional[str]
    composite_risk: float
    risk_tier: str
    axis_count: int
    model_version: str
    updated: bool


class BatchScoringResponse(BaseModel):
    processed: int
    updated: int
    skipped: int
    failed: int
    errors: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str = "mcp_llm_axis_scoring_consumer"
    last_run: Optional[datetime] = None


class TriggerResponse(BaseModel):
    processed: int
    updated: int
    skipped: int
    failed: int
    errors: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Core computation (pure, no DB)
# --------------------------------------------------------------------------- #
def compute_axis_scores(
    rows: List[McpLlmAxisScore],
) -> List[AxisScoreOut]:
    """Convert ORM rows to typed response models."""
    results: List[AxisScoreOut] = []
    for row in rows:
        composite = _composite_risk(row.p_top, row.p_critical, row.p_danger)
        results.append(
            AxisScoreOut(
                axis_name=row.axis_name,
                label=row.label,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger,
                composite=composite,
                escalated=row.escalated,
                model_version=row.model_version,
                scored_at=row.scored_at,
            )
        )
    return results


def compute_composite_tier(rows: List[McpLlmAxisScore]) -> tuple[float, str]:
    """Average composite across axes and assign tier."""
    if not rows:
        return 0.0, "low"
    composites = [
        _composite_risk(r.p_top, r.p_critical, r.p_danger)
        for r in rows
    ]
    avg = sum(composites) / len(composites)
    return round(avg, 6), _assign_tier(avg)


def score_server(
    server_id: str,
    db: Session,
    current_model_version: Optional[str] = None,
) -> Optional[ServerScoringResult]:
    """Score a single server: read latest axes, compute tier, update registry."""
    if current_model_version is None:
        # pick the most recent model version
        row = (
            db.execute(
                select(McpLlmAxisScore.model_version)
                .where(McpLlmAxisScore.server_id == server_id)
                .order_by(McpLlmAxisScore.scored_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        current_model_version = row

    rows = (
        db.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id == server_id)
            .where(McpLlmAxisScore.model_version == current_model_version)
            .where(McpLlmAxisScore.axis_name.in_(ALL_AXES))
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    composite, tier = compute_composite_tier(rows)

    server = (
        db.execute(
            select(McpServerRegistry)
            .where(McpServerRegistry.server_id == server_id)
        )
        .scalars()
        .first()
    )
    server_name = getattr(server, "name", None) if server else None
    updated = False

    if server:
        prev_tier = getattr(server, "risk_tier", None)
        if prev_tier != tier:
            server.risk_tier = tier
            updated = True

    return ServerScoringResult(
        server_id=server_id,
        server_name=server_name,
        composite_risk=composite,
        risk_tier=tier,
        axis_count=len(rows),
        model_version=current_model_version,
        updated=updated,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/mcp-llm-axis-scoring/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="ok")


@router.get(
    "/mcp-llm-axis-scoring/servers/{server_id}",
    response_model=ServerScoringResult,
)
async def score_server_endpoint(
    server_id: str,
    model_version: Optional[str] = Query(None, description="Specific model version to score against"),
    db: Session = Depends(get_session),
) -> ServerScoringResult:
    """Score a single server and return the result."""
    result = score_server(server_id, db, model_version)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server {server_id}",
        )
    return result


@router.get(
    "/mcp-llm-axis-scoring/servers/{server_id}/axes",
    response_model=List[AxisScoreOut],
)
async def get_server_axes(
    server_id: str,
    model_version: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> List[AxisScoreOut]:
    """Return all axis scores for a server."""
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name.in_(ALL_AXES))
        .order_by(McpLlmAxisScore.scored_at.desc())
    )
    if model_version:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)

    rows = db.execute(stmt).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No axis scores for server {server_id}")
    return compute_axis_scores(list(rows))


@router.get(
    "/mcp-llm-axis-scoring/batch",
    response_model=BatchScoringResponse,
)
async def batch_score_servers(
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> BatchScoringResponse:
    """Batch-score all servers that have axis scores in the lookback window."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    server_ids = (
        db.execute(
            select(McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .distinct()
        )
        .scalars()
        .all()
    )

    processed, updated, skipped, failed = 0, 0, 0, 0
    errors: List[str] = []

    for sid in server_ids:
        processed += 1
        try:
            result = score_server(sid, db)
            if result is None:
                skipped += 1
            elif result.updated:
                updated += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{sid}: {exc}")

    if updated > 0:
        db.commit()

    return BatchScoringResponse(
        processed=processed,
        updated=updated,
        skipped=skipped,
        failed=failed,
        errors=errors,
    )


@router.post("/mcp-llm-axis-scoring/trigger", response_model=TriggerResponse)
async def trigger_scoring(
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> TriggerResponse:
    """Alias for batch_score_servers (trigger endpoint)."""
    result = await batch_score_servers(lookback_days, db)
    return TriggerResponse(**result.model_dump())


# --------------------------------------------------------------------------- #
# Daemon loop
# --------------------------------------------------------------------------- #
def run() -> None:
    """Background daemon: score all servers, heartbeat every 60 s."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info(
        "Starting mcp_llm_axis_scoring_consumer daemon "
        "(write_service=%s, heartbeat_interval=%ds)",
        WRITE_SERVICE_URL,
        HEARTBEAT_INTERVAL,
    )

    consecutive_failures = 0
    max_consecutive_failures = 5
    lookback_days = 7

    while True:
        cycle_start = time.time()

        try:
            with _db_session() as db:
                from datetime import timedelta

                cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                server_ids = (
                    db.execute(
                        select(McpLlmAxisScore.server_id)
                        .where(McpLlmAxisScore.scored_at >= cutoff)
                        .distinct()
                    )
                    .scalars()
                    .all()
                )

            logger.info("Scoring cycle: %d servers", len(server_ids))
            updated_count = 0

            for sid in server_ids:
                with _db_session() as db:
                    result = score_server(sid, db)
                    if result and result.updated:
                        updated_count += 1

            if updated_count > 0:
                with _db_session() as db:
                    db.commit()
                logger.info("Updated risk_tier for %d servers", updated_count)

            consecutive_failures = 0
            logger.info("Cycle complete: %d processed, %d updated", len(server_ids), updated_count)

        except Exception as exc:
            consecutive_failures += 1
            logger.error(
                "Cycle failed (%d/%d): %s",
                consecutive_failures,
                max_consecutive_failures,
                exc,
            )
            if consecutive_failures >= max_consecutive_failures:
                logger.critical("Max consecutive failures reached -- exiting")
                break

        if not _send_heartbeat():
            logger.warning("Heartbeat failed")

        elapsed = time.time() - cycle_start
        sleep_time = max(0.0, HEARTBEAT_INTERVAL - elapsed)
        logger.debug("Cycle %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _run_self_test()


def _run_self_test() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Seed test data
    db = TestSession()
    db.add(McpServerRegistry(
        server_id="srv-test-001",
        name="Test Server Alpha",
        risk_tier=None,
        registry_source="test",
    ))
    db.add(McpServerRegistry(
        server_id="srv-test-002",
        name="Test Server Beta",
        risk_tier="medium",
        registry_source="test",
    ))

    now = datetime.now(timezone.utc)
    for axis in ALL_AXES:
        p_top = 0.5 + (hash(f"srv-test-001{axis}") % 100) / 500.0
        p_crit = 0.1 + (hash(f"srv-test-001{axis}crit") % 50) / 500.0
        p_dang = 0.2 + (hash(f"srv-test-001{axis}dng") % 80) / 400.0
        db.add(McpLlmAxisScore(
            server_id="srv-test-001",
            axis_name=axis,
            label="elevated",
            label_index=1,
            p_top=round(p_top, 4),
            p_critical=round(p_crit, 4),
            p_danger=round(p_dang, 4),
            escalated=False,
            model_version="v1",
            adapter_sha256="abc123",
            scored_at=now,
        ))

    db.commit()
    db.close()

    # Test 1: health
    r = client.get("/api/mcp-llm-axis-scoring/health")
    assert r.status_code == 200, f"health: {r.status_code} {r.text}"
    assert r.json()["status"] == "ok", r.json()

    # Test 2: score a known server
    r = client.get("/api/mcp-llm-axis-scoring/servers/srv-test-001")
    assert r.status_code == 200, f"score: {r.status_code} {r.text}"
    data = r.json()
    assert data["server_id"] == "srv-test-001"
    assert data["axis_count"] == len(ALL_AXES)
    assert data["risk_tier"] in ("low", "medium", "high", "critical"), f"Invalid tier: {data['risk_tier']}"
    assert 0.0 <= data["composite_risk"] <= 1.0, f"Invalid composite: {data['composite_risk']}"

    # Test 3: get axes
    r = client.get("/api/mcp-llm-axis-scoring/servers/srv-test-001/axes")
    assert r.status_code == 200, f"axes: {r.status_code} {r.text}"
    axes = r.json()
    assert len(axes) == len(ALL_AXES), f"Expected {len(ALL_AXES)} axes, got {len(axes)}"
    for ax in axes:
        assert "composite" in ax
        assert "axis_name" in ax

    # Test 4: 404 for unknown server
    r = client.get("/api/mcp-llm-axis-scoring/servers/no-such-server")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 5: batch scoring
    r = client.get("/api/mcp-llm-axis-scoring/batch", params={"lookback_days": 7})
    assert r.status_code == 200, f"batch: {r.status_code} {r.text}"
    batch = r.json()
    assert batch["processed"] >= 1
    assert batch["updated"] >= 1  # srv-test-001 had null tier

    # Test 6: trigger (alias)
    r = client.post("/api/mcp-llm-axis-scoring/trigger", params={"lookback_days": 7})
    assert r.status_code == 200, f"trigger: {r.status_code} {r.text}"
    assert "processed" in r.json()

    # Test 7: tier was written back
    db = TestSession()
    srv = db.execute(select(McpServerRegistry).where(McpServerRegistry.server_id == "srv-test-001")).scalars().first()
    assert srv is not None
    assert srv.risk_tier in ("low", "medium", "high", "critical"), f"Unexpected tier: {srv.risk_tier}"
    db.close()

    return True


if __name__ == "__main__":
    try:
        ok = _run_self_test()
    except Exception as exc:
        print(f"FAIL: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    if ok:
        print("PASS")
        sys.exit(0)
