# deps: fastapi, pydantic, sqlalchemy, requests
"""
axis_score_freshness_scoring_consumer — FastAPI daemon.

Reads axis scores from mcp_llm_axis_scores via the app DB, computes a freshness
score (0–100, higher = fresher) for each server using exponential decay on the
time since last scoring, and writes mcp_axis_score_freshness records to
write_service at 127.0.0.1:8772.

Health heartbeat fires every ≤60 s regardless of work-cycle outcome.
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure repo root on path so app.* imports work from any working directory
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds
PROCESSING_CADENCE = 4 * 3600  # 4 hours
REQUEST_TIMEOUT = 10
WRITE_TIMEOUT = 30

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["axis_score_freshness_scoring_consumer"])

_last_heartbeat: dict = {"value": None}
_last_run: dict = {"value": None}

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ServerFreshnessScore(BaseModel):
    server_id: str
    freshness_score: float = Field(..., ge=0.0, le=100.0)
    freshness_label: str = Field(
        ...,
        description="CRITICAL(0-20) | HIGH(20-40) | MEDIUM(40-70) | LOW(70-100)",
    )
    age_seconds: int = Field(..., ge=0)
    last_scored: datetime
    computed_at: datetime


class FreshnessProcessResult(BaseModel):
    processed: int
    skipped: int
    failed: int


class HealthResponse(BaseModel):
    status: str
    service: str
    last_heartbeat: Optional[str] = None
    last_run: Optional[str] = None


# --------------------------------------------------------------------------- #
# Pure freshness scoring logic (no DB, no network)
# --------------------------------------------------------------------------- #


def compute_freshness_score(age_seconds: float, half_life_seconds: float = 24 * 3600) -> float:
    """
    Exponential-decay freshness score in [0, 100].
    half_life_seconds: time after which freshness drops to 50.
    """
    if age_seconds <= 0:
        return 100.0
    score = 100.0 * math.exp(-math.log(2) * age_seconds / half_life_seconds)
    return round(score, 4)


def label_from_score(score: float) -> str:
    """Map freshness score to a human-readable label."""
    if score < 20:
        return "CRITICAL"
    if score < 40:
        return "HIGH"
    if score < 70:
        return "MEDIUM"
    return "LOW"


# --------------------------------------------------------------------------- #
# Network helpers
# --------------------------------------------------------------------------- #


def _send_heartbeat() -> bool:
    _last_heartbeat["value"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": [
                    {
                        "service": "axis_score_freshness_scoring_consumer",
                        "status": "running",
                        "last_heartbeat": _last_heartbeat["value"],
                        "meta": {"last_run": _last_run.get("value")},
                    }
                ],
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException as exc:
        logger.warning("Heartbeat failed: %s", exc)
        return False


def _heartbeat_loop() -> None:
    while True:
        _send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def _write_freshness_records(rows: list[dict]) -> bool:
    if not rows:
        return True
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "mcp_axis_score_freshness", "rows": rows},
            timeout=WRITE_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException as exc:
        logger.error("Failed to write freshness records: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Core scoring (session-scoped; called from both endpoints and daemon)
# --------------------------------------------------------------------------- #


def _fetch_latest_scores(db: Session) -> list[ServerFreshnessScore]:
    """
    For every server that has at least one mcp_llm_axis_scores row,
    find its most-recent scored_at and compute freshness.
    """
    from sqlalchemy import func

    # Subquery: latest scored_at per server_id
    sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = (
        db.query(McpLlmAxisScore, McpServerRegistry.name)
        .join(sub, McpLlmAxisScore.server_id == sub.c.server_id)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(
            McpLlmAxisScore.scored_at == sub.c.latest,
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    records: list[ServerFreshnessScore] = []

    for score_row, _ in rows:
        scored_at = score_row.scored_at or now
        # Use naive datetime for age calculation to match scored_at storage
        if scored_at.tzinfo is not None:
            scored_at_naive = scored_at.replace(tzinfo=None)
        else:
            scored_at_naive = scored_at
        if now.tzinfo is not None:
            now_naive = now.replace(tzinfo=None)
        else:
            now_naive = now
        age_seconds = max(0, int((now_naive - scored_at_naive).total_seconds()))
        freshness_score = compute_freshness_score(float(age_seconds))
        label = label_from_score(freshness_score)
        records.append(
            ServerFreshnessScore(
                server_id=score_row.server_id,
                freshness_score=freshness_score,
                freshness_label=label,
                age_seconds=age_seconds,
                last_scored=scored_at,
                computed_at=now,
            )
        )
    return records


def process_freshness_scores(db: Session) -> FreshnessProcessResult:
    """Compute freshness scores for all servers and write to write_service."""
    records = _fetch_latest_scores(db)
    if not records:
        logger.info("No axis scores found for freshness computation")
        return FreshnessProcessResult(processed=0, skipped=0, failed=0)

    rows = [
        {
            "server_id": r.server_id,
            "freshness_score": r.freshness_score,
            "freshness_label": r.freshness_label,
            "age_seconds": r.age_seconds,
            "last_scored": (
                r.last_scored.isoformat() if r.last_scored else None
            ),
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
        }
        for r in records
    ]
    ok = _write_freshness_records(rows)
    if ok:
        logger.info("Wrote %d freshness records", len(rows))
        return FreshnessProcessResult(processed=len(records), skipped=0, failed=0)
    else:
        logger.error("Failed to write freshness records")
        return FreshnessProcessResult(processed=0, skipped=0, failed=len(records))


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #


@router.get("/axis-freshness/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(
        status="healthy",
        service="axis_score_freshness_scoring_consumer",
        last_heartbeat=_last_heartbeat.get("value"),
        last_run=_last_run.get("value"),
    )


@router.post("/axis-freshness/trigger", response_model=FreshnessProcessResult)
def trigger(db: Session = Depends(get_session)) -> FreshnessProcessResult:
    """Manually trigger a freshness scoring cycle."""
    return process_freshness_scores(db)


@router.get(
    "/axis-freshness/server/{server_id}",
    response_model=ServerFreshnessScore,
    summary="Get freshness score for one server",
)
def get_server_freshness(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerFreshnessScore:
    """Return the freshness score for a specific server."""
    from sqlalchemy import func

    sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    score_row = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at == sub.c.latest,
        )
        .first()
    )

    now = datetime.now(timezone.utc)
    if not score_row:
        # No scoring history: max age
        return ServerFreshnessScore(
            server_id=server_id,
            freshness_score=0.0,
            freshness_label="CRITICAL",
            age_seconds=0,
            last_scored=now,
            computed_at=now,
        )

    scored_at = score_row.scored_at or now
    if scored_at.tzinfo is not None:
        scored_at_naive = scored_at.replace(tzinfo=None)
    else:
        scored_at_naive = scored_at
    if now.tzinfo is not None:
        now_naive = now.replace(tzinfo=None)
    else:
        now_naive = now
    age_seconds = max(0, int((now_naive - scored_at_naive).total_seconds()))
    freshness_score = compute_freshness_score(float(age_seconds))
    label = label_from_score(freshness_score)

    return ServerFreshnessScore(
        server_id=server_id,
        freshness_score=freshness_score,
        freshness_label=label,
        age_seconds=age_seconds,
        last_scored=scored_at,
        computed_at=now,
    )


# --------------------------------------------------------------------------- #
# Daemon entrypoint
# --------------------------------------------------------------------------- #


def run() -> None:
    """Background daemon: heartbeat + processing loop. Blocks forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info(
        "Starting axis_score_freshness_scoring_consumer "
        "(write_service=%s, cadence=%ds)",
        WRITE_SERVICE_URL,
        PROCESSING_CADENCE,
    )

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    while True:
        cycle_start = time.time()
        _last_run["value"] = datetime.now(timezone.utc).isoformat()

        try:
            with get_session() as db:
                result = process_freshness_scores(db)
            logger.info(
                "Cycle complete: processed=%d, skipped=%d, failed=%d",
                result.processed,
                result.skipped,
                result.failed,
            )
        except Exception as exc:
            logger.error("Cycle failed: %s", exc)

        elapsed = time.time() - cycle_start
        sleep_time = max(1, PROCESSING_CADENCE - elapsed)
        logger.debug("Cycle %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # --- Pure-function unit tests ---
    # Zero age -> 100
    assert compute_freshness_score(0.0) == 100.0, "zero age"
    # Half-life: 1 day -> score ~= 50
    score_half = compute_freshness_score(24 * 3600)
    assert 49.5 < score_half < 50.5, f"half-life: {score_half}"
    # 7 days with 24h half-life -> 100 * 0.5^7 ~= 0.78
    score_week = compute_freshness_score(7 * 24 * 3600)
    assert 0.7 < score_week < 0.9, f"week: {score_week}"
    # Very old -> close to 0
    score_old = compute_freshness_score(100 * 24 * 3600)
    assert score_old < 0.1, f"very old: {score_old}"

    # Label tests
    assert label_from_score(10) == "CRITICAL"
    assert label_from_score(20) == "HIGH"
    assert label_from_score(50) == "MEDIUM"
    assert label_from_score(80) == "LOW"
    assert label_from_score(100) == "LOW"

    print("  PASS pure functions")

    # --- FastAPI endpoint tests with SQLite override ---
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    that_app = FastAPI()

    def override_get_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)
    client = TestClient(that_app)

    # Health
    resp = client.get("/api/axis-freshness/health")
    assert resp.status_code == 200, f"health: {resp.status_code}"
    assert resp.json()["status"] == "healthy", resp.json()
    print("  PASS /health")

    # Trigger with no data
    resp = client.post("/api/axis-freshness/trigger")
    assert resp.status_code == 200, f"trigger: {resp.status_code}"
    data = resp.json()
    assert "processed" in data and "skipped" in data and "failed" in data, data
    assert data["processed"] == 0, data
    print("  PASS /trigger (empty)")

    # Seed data and trigger again
    now = datetime.now(timezone.utc)
    earlier = now - __import__("datetime").timedelta(hours=6)

    session = TestingSessionLocal()
    session.add(
        McpServerRegistry(
            server_id="srv-fresh",
            name="Fresh Server",
            registry_source="selftest",
            url="https://test.example",
        )
    )
    session.add(
        McpLlmAxisScore(
            id=10,
            server_id="srv-fresh",
            axis_name="overall_risk",
            label="low",
            label_index=0,
            p_top=0.2,
            p_critical=0.05,
            p_danger=0.1,
            model_version="test",
            adapter_sha256="deadbeef",
            scored_at=earlier,
        )
    )
    session.add(
        McpLlmAxisScore(
            id=11,
            server_id="srv-fresh",
            axis_name="auth_strength",
            label="low",
            label_index=0,
            p_top=0.3,
            p_critical=0.1,
            p_danger=0.15,
            model_version="test",
            adapter_sha256="deadbeef",
            scored_at=earlier,
        )
    )
    session.commit()
    session.close()

    resp = client.post("/api/axis-freshness/trigger")
    assert resp.status_code == 200, f"trigger with data: {resp.status_code}"
    data = resp.json()
    assert data["processed"] == 1, f"expected 1 server, got {data}"
    print("  PASS /trigger (1 server)")

    # Server-specific endpoint
    resp = client.get("/api/axis-freshness/server/srv-fresh")
    assert resp.status_code == 200, f"server freshness: {resp.status_code}"
    body = resp.json()
    assert body["server_id"] == "srv-fresh"
    assert 0.0 <= body["freshness_score"] <= 100.0, body
    assert body["freshness_label"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW"), body
    assert body["age_seconds"] > 0, body
    print(f"  PASS /server/srv-fresh -> {body['freshness_label']} ({body['freshness_score']:.2f})")

    # Unknown server
    resp = client.get("/api/axis-freshness/server/no-such-server")
    assert resp.status_code == 200, f"unknown server: {resp.status_code}"
    body = resp.json()
    assert body["server_id"] == "no-such-server"
    assert body["freshness_label"] == "CRITICAL", body
    print("  PASS /server/unknown -> CRITICAL")

    print("\nPASS")
