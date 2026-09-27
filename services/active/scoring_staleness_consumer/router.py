# deps: fastapi, pydantic, sqlalchemy, requests
"""
scoring_staleness_consumer — FastAPI daemon.

Reads servers from mcp_server_registry and their axis scores from
mcp_llm_axis_scores (app DB). Identifies servers whose most recent score
is older than the configurable threshold (default 7 days). Writes
stale-server events to write_service at 127.0.0.1:8772.

Health heartbeat fires every ≤60 s regardless of work-cycle outcome.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds
PROCESSING_CADENCE = 5 * 60  # 5 minutes
REQUEST_TIMEOUT = 10
WRITE_TIMEOUT = 30
DEFAULT_STALE_THRESHOLD_DAYS = 7

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scoring_staleness_consumer"])

_last_heartbeat: dict = {"value": None}
_last_run: dict = {"value": None}


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class StaleServerEvent(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    name: Optional[str] = Field(None, description="Server display name")
    registry_source: Optional[str] = Field(None, description="Source of the server entry")
    url: Optional[str] = Field(None, description="Server URL")
    risk_tier: Optional[str] = Field(None, description="Current risk tier")
    last_scored_at: Optional[datetime] = Field(None, description="Most recent score timestamp")
    days_since_last_score: int = Field(..., ge=0, description="Number of days since last score")
    stale_threshold_days: int = Field(..., description="Threshold used for staleness")
    flagged_at: datetime = Field(..., description="When this event was created")


class StaleServersResponse(BaseModel):
    flagged: List[StaleServerEvent] = Field(default_factory=list)
    total_scanned: int = Field(0, description="Total servers scanned in this cycle")
    threshold_days: int = Field(..., description="Staleness threshold used")
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class TriggerResponse(BaseModel):
    status: str = Field(..., description="ok | skipped | error")
    processed: int = Field(0, description="Number of stale servers detected and written")
    skipped: int = Field(0, description="Number of servers skipped")
    failed: int = Field(0, description="Number of write failures")
    message: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = Field(..., description="healthy | degraded | error")
    service: str = Field(default="scoring_staleness_consumer")
    last_heartbeat: Optional[str] = None
    last_run: Optional[str] = None


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
                        "service": "scoring_staleness_consumer",
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


def _write_stale_events(rows: list[dict]) -> bool:
    if not rows:
        return True
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "scoring_staleness_events", "rows": rows},
            timeout=WRITE_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException as exc:
        logger.error("Failed to write stale events: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #

def _build_stale_events(
    db: Session,
    threshold_days: int,
) -> tuple[List[StaleServerEvent], int]:
    """
    Find servers whose most-recent axis score is older than threshold_days.
    Returns (events, total_servers_with_scores).
    """
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=threshold_days)

    # Subquery: latest scored_at per server_id
    from sqlalchemy import func, select

    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_score_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join with registry
    rows = (
        db.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.registry_source,
                McpServerRegistry.url,
                McpServerRegistry.risk_tier,
                latest_subq.c.latest_score_at,
            )
            .select_from(McpServerRegistry)
            .join(
                latest_subq,
                McpServerRegistry.server_id == latest_subq.c.server_id,
            )
        )
        .fetchall()
    )

    events: List[StaleServerEvent] = []
    for row in rows:
        (sid, name, source, url, risk_tier, latest_score_at) = row
        if latest_score_at is None:
            continue
        # Make naive for subtraction to avoid tzinfo mismatch
        if latest_score_at.tzinfo is not None:
            latest_naive = latest_score_at.replace(tzinfo=None)
        else:
            latest_naive = latest_score_at
        now_naive = now.replace(tzinfo=None) if now.tzinfo else now
        delta = now_naive - latest_naive
        days_since = max(0, delta.days)
        if latest_score_at < threshold:
            flagged_at = datetime.utcnow()
            events.append(
                StaleServerEvent(
                    server_id=sid,
                    name=name,
                    registry_source=source,
                    url=url,
                    risk_tier=risk_tier,
                    last_scored_at=latest_score_at,
                    days_since_last_score=days_since,
                    stale_threshold_days=threshold_days,
                    flagged_at=flagged_at,
                )
            )

    return events, len(rows)


def _run_cycle(db: Session) -> tuple[int, int, int]:
    """
    Execute one staleness detection cycle.
    Returns (processed, skipped, failed).
    """
    threshold_days = int(os.environ.get("STALE_THRESHOLD_DAYS", DEFAULT_STALE_THRESHOLD_DAYS))
    events, total_scanned = _build_stale_events(db, threshold_days)

    if not events:
        logger.info("No stale servers detected (scanned %d servers)", total_scanned)
        return 0, total_scanned, 0

    rows = [
        {
            "server_id": e.server_id,
            "name": e.name,
            "registry_source": e.registry_source,
            "url": e.url,
            "risk_tier": e.risk_tier,
            "last_scored_at": (
                e.last_scored_at.isoformat() if e.last_scored_at else None
            ),
            "days_since_last_score": e.days_since_last_score,
            "stale_threshold_days": e.stale_threshold_days,
            "flagged_at": e.flagged_at.isoformat() if e.flagged_at else None,
        }
        for e in events
    ]

    ok = _write_stale_events(rows)
    if ok:
        logger.info(
            "Wrote %d stale events for %d scanned servers (threshold=%d days)",
            len(events), total_scanned, threshold_days,
        )
        return len(events), total_scanned - len(events), 0
    else:
        logger.error("Failed to write stale events to write_service")
        return 0, total_scanned, len(events)


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #

@router.get("/scoring-staleness/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(
        status="healthy",
        service="scoring_staleness_consumer",
        last_heartbeat=_last_heartbeat.get("value"),
        last_run=_last_run.get("value"),
    )


@router.post(
    "/scoring-staleness/trigger",
    response_model=TriggerResponse,
    summary="Manually trigger a staleness detection cycle",
)
def trigger_staleness(
    threshold_days: Optional[int] = None,
    db: Session = Depends(get_session),
) -> TriggerResponse:
    """
    Manually invoke one staleness detection cycle.
    Optionally override threshold_days via query param (default from env / 7 days).
    """
    if threshold_days is None:
        threshold_days = int(
            os.environ.get("STALE_THRESHOLD_DAYS", DEFAULT_STALE_THRESHOLD_DAYS)
        )
    threshold_days = max(1, threshold_days)

    events, total = _build_stale_events(db, threshold_days)

    if not events:
        return TriggerResponse(
            status="skipped",
            processed=0,
            skipped=total,
            failed=0,
            message="No stale servers found",
        )

    rows = [
        {
            "server_id": e.server_id,
            "name": e.name,
            "registry_source": e.registry_source,
            "url": e.url,
            "risk_tier": e.risk_tier,
            "last_scored_at": (
                e.last_scored_at.isoformat() if e.last_scored_at else None
            ),
            "days_since_last_score": e.days_since_last_score,
            "stale_threshold_days": e.stale_threshold_days,
            "flagged_at": e.flagged_at.isoformat() if e.flagged_at else None,
        }
        for e in events
    ]

    ok = _write_stale_events(rows)
    if ok:
        return TriggerResponse(
            status="ok",
            processed=len(events),
            skipped=total - len(events),
            failed=0,
        )
    else:
        return TriggerResponse(
            status="error",
            processed=0,
            skipped=total,
            failed=len(events),
            message="Failed to write events to write_service",
        )


@router.get(
    "/scoring-staleness/list",
    response_model=StaleServersResponse,
    summary="List stale servers without writing events",
)
def list_stale_servers(
    threshold_days: Optional[int] = None,
    db: Session = Depends(get_session),
) -> StaleServersResponse:
    """
    Return stale servers without writing events to write_service.
    Useful for inspection without side-effects.
    """
    if threshold_days is None:
        threshold_days = int(
            os.environ.get("STALE_THRESHOLD_DAYS", DEFAULT_STALE_THRESHOLD_DAYS)
        )
    threshold_days = max(1, threshold_days)

    events, total = _build_stale_events(db, threshold_days)
    return StaleServersResponse(
        flagged=events,
        total_scanned=total,
        threshold_days=threshold_days,
        generated_at=datetime.utcnow(),
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
        "Starting scoring_staleness_consumer "
        "(write_service=%s, cadence=%ds, threshold=%sd)",
        WRITE_SERVICE_URL,
        PROCESSING_CADENCE,
        os.environ.get("STALE_THRESHOLD_DAYS", str(DEFAULT_STALE_THRESHOLD_DAYS)),
    )

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    while True:
        cycle_start = time.time()
        _last_run["value"] = datetime.now(timezone.utc).isoformat()

        try:
            with get_session() as db:
                processed, skipped, failed = _run_cycle(db)
            logger.info(
                "Cycle complete: processed=%d, skipped=%d, failed=%d",
                processed, skipped, failed,
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
    # --- Pure-function sanity checks ---
    # Events build correctly from in-memory data
    now = datetime.utcnow()
    old = now - timedelta(days=10)
    recent = now - timedelta(days=1)

    # Build test engine
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override_session() -> Generator[Session, None, None]:
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    # Seed data
    with TestSessionLocal() as sess:
        # Fresh server (scored yesterday)
        sess.add(McpServerRegistry(
            server_id="srv-fresh", name="Fresh Server",
            registry_source="test", url="http://fresh.test",
            risk_tier="LOW",
        ))
        sess.add(McpLlmAxisScore(
            id=1, server_id="srv-fresh", axis_name="overall_risk",
            label="low", label_index=0, p_top=0.9, p_critical=0.0,
            p_danger=0.05, probs=None, model_version="v1",
            adapter_sha256="deadbeef", scored_at=recent,
            decision_rule_version="r1", escalated=False, escalated_to=None,
        ))
        # Stale server (scored 10 days ago)
        sess.add(McpServerRegistry(
            server_id="srv-stale", name="Stale Server",
            registry_source="test", url="http://stale.test",
            risk_tier="HIGH",
        ))
        sess.add(McpLlmAxisScore(
            id=2, server_id="srv-stale", axis_name="overall_risk",
            label="high", label_index=3, p_top=0.1, p_critical=0.7,
            p_danger=0.15, probs=None, model_version="v1",
            adapter_sha256="deadbeef", scored_at=old,
            decision_rule_version="r1", escalated=False, escalated_to=None,
        ))
        # Never-scored server (no mcp_llm_axis_scores rows)
        sess.add(McpServerRegistry(
            server_id="srv-never", name="Never Scored",
            registry_source="test", url="http://never.test",
            risk_tier="MEDIUM",
        ))
        sess.commit()

    # Test FastAPI endpoints with override
    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = _override_session
    that_app.include_router(router)
    client = TestClient(that_app)

    # Health
    resp = client.get("/api/scoring-staleness/health")
    assert resp.status_code == 200, f"health: {resp.status_code}"
    assert resp.json()["status"] == "healthy", resp.json()
    print("  PASS /health")

    # List stale servers (default threshold 7 days)
    resp = client.get("/api/scoring-staleness/list")
    assert resp.status_code == 200, f"list: {resp.status_code}"
    data = resp.json()
    assert data["threshold_days"] == 7, data
    assert len(data["flagged"]) == 1, f"expected 1 stale, got {len(data['flagged'])}"
    assert data["flagged"][0]["server_id"] == "srv-stale", data
    assert data["flagged"][0]["days_since_last_score"] >= 7, data
    print("  PASS /list (default 7d) — found srv-stale")

    # List stale servers (custom threshold 14 days — srv-stale should not appear)
    resp = client.get("/api/scoring-staleness/list?threshold_days=14")
    assert resp.status_code == 200, f"list(14d): {resp.status_code}"
    data = resp.json()
    assert data["threshold_days"] == 14, data
    assert len(data["flagged"]) == 0, f"expected 0 stale at 14d, got {data['flagged']}"
    print("  PASS /list (14d) — no stale servers")

    # Trigger (no write_service, so we expect error status)
    resp = client.post("/api/scoring-staleness/trigger")
    assert resp.status_code == 200, f"trigger: {resp.status_code}"
    data = resp.json()
    # write_service is not running in self-test, so failed=1 expected
    # (processed=1 if write succeeded, or processed=0 if write failed)
    assert data["status"] in ("ok", "error"), f"unexpected status: {data}"
    assert data["processed"] + data["failed"] == 1, f"expected 1 event, got {data}"
    print(f"  PASS /trigger — status={data['status']}")

    # Verify pure _build_stale_events logic directly
    with TestSessionLocal() as sess:
        events_7d, total = _build_stale_events(sess, threshold_days=7)
        assert len(events_7d) == 1, f"7d: expected 1 stale, got {len(events_7d)}"
        assert events_7d[0].server_id == "srv-stale", events_7d[0]
        assert total == 2, f"total servers with scores: expected 2, got {total}"

        events_20d, total = _build_stale_events(sess, threshold_days=20)
        assert len(events_20d) == 0, f"20d: expected 0 stale, got {len(events_20d)}"
    print("  PASS _build_stale_events pure logic")

    print("\nPASS")
    sys.exit(0)
