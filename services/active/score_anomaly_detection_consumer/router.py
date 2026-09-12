# deps: fastapi, pydantic, sqlalchemy, requests
"""score_anomaly_detection_consumer — FastAPI daemon.

Detects statistical anomalies in LLM axis scores for MCP servers by comparing
individual axis p_top distributions against per-axis population statistics
within a configurable time window. Flags axes where |z-score| exceeds a
configurable threshold.

Data: mcp_llm_axis_scores, mcp_server_registry via app.db SQLAlchemy session.
Auth: public (PRODUCT_SPEC §9 scope).
Heartbeat: fires every ≤60 s regardless of work-cycle outcome.
"""
from __future__ import annotations

import logging
import statistics
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_URL = f"{WRITE_SERVICE_URL}/service_health"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
REQUEST_TIMEOUT = 10
WRITE_TIMEOUT = 30
HEARTBEAT_INTERVAL = 60  # seconds
PROCESSING_INTERVAL = 300  # seconds
DEFAULT_WINDOW_HOURS = 72
DEFAULT_Z_THRESHOLD = 2.0

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/score_anomaly_detection_consumer",
    tags=["score_anomaly_detection_consumer"],
)

# --------------------------------------------------------------------------- #
# State shared with daemon threads
# --------------------------------------------------------------------------- #
_last_heartbeat: dict = {"value": None}
_last_run: dict = {"value": None}

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class AxisAnomalyScore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    baseline_mean: float
    baseline_std: float
    z_score: float
    deviation_pct: float
    is_anomalous: bool
    anomaly_direction: Optional[str] = None


class ServerAnomalyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    server_name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    anomaly_count: int
    total_axes: int
    axes: List[AxisAnomalyScore]
    model_version: Optional[str] = None
    computed_at: datetime


class AnomalyListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_servers: int
    anomalous_servers: int
    anomaly_servers: List[ServerAnomalyResponse]
    computed_at: datetime


class TriggerResponse(BaseModel):
    processed: int
    anomalies_found: int
    written: int
    failed: int


class HealthResponse(BaseModel):
    status: str
    service: str
    last_heartbeat: Optional[str] = None
    last_run: Optional[str] = None


# --------------------------------------------------------------------------- #
# Core anomaly detection (pure — no DB, no network)
# --------------------------------------------------------------------------- #


def _compute_z(p_top: float, mean: float, std: float) -> float:
    if std == 0.0:
        return 0.0
    return (p_top - mean) / std


def _deviation_pct(p_top: float, baseline: float) -> float:
    if baseline == 0.0:
        return 0.0
    return abs(p_top - baseline) / baseline * 100.0


def _anomaly_direction(p_top: float, baseline: float) -> Optional[str]:
    if p_top > baseline:
        return "high"
    if p_top < baseline:
        return "low"
    return None


def _build_axis_anomaly(
    row: McpLlmAxisScore,
    baseline_mean: float,
    baseline_std: float,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> AxisAnomalyScore:
    p_top = row.p_top if row.p_top is not None else 0.0
    z = _compute_z(p_top, baseline_mean, baseline_std)
    is_anomalous = abs(z) > z_threshold
    direction = _anomaly_direction(p_top, baseline_mean) if is_anomalous else None
    dev_pct = _deviation_pct(p_top, baseline_mean)
    return AxisAnomalyScore(
        axis_name=row.axis_name,
        label=row.label,
        label_index=row.label_index,
        p_top=row.p_top,
        p_critical=row.p_critical,
        p_danger=row.p_danger,
        baseline_mean=round(baseline_mean, 6),
        baseline_std=round(baseline_std, 6),
        z_score=round(z, 4),
        deviation_pct=round(dev_pct, 4),
        is_anomalous=is_anomalous,
        anomaly_direction=direction,
    )


def _population_stats(
    db: Session,
    axis_name: str,
    model_version: str,
    cutoff: Optional[datetime] = None,
) -> tuple[float, float]:
    """Return (mean, stdev) of p_top for a given axis+model_version."""
    q = db.query(McpLlmAxisScore.p_top).filter(
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.model_version == model_version,
        McpLlmAxisScore.p_top.isnot(None),
    )
    if cutoff:
        q = q.filter(McpLlmAxisScore.scored_at >= cutoff)
    p_vals = [r[0] for r in q.all()]
    if len(p_vals) >= 2:
        return statistics.mean(p_vals), statistics.stdev(p_vals)
    if len(p_vals) == 1:
        return p_vals[0], 0.0
    return 0.0, 0.0


# --------------------------------------------------------------------------- #
# Internal helpers (network I/O only — safe to call from run())
# --------------------------------------------------------------------------- #

def _send_heartbeat() -> bool:
    _last_heartbeat["value"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.post(
            SERVICE_HEALTH_URL,
            json={
                "service": "score_anomaly_detection_consumer",
                "status": "running",
                "timestamp": _last_heartbeat["value"],
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code in (200, 201, 202)
    except requests.RequestException as exc:
        logger.warning("Heartbeat failed: %s", exc)
        return False


def _write_anomaly_rows(rows: List[dict]) -> int:
    if not rows:
        return 0
    payload = {
        "table": "mcp_score_anomalies",
        "rows": rows,
        "wait": True,
    }
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=WRITE_TIMEOUT)
        resp.raise_for_status()
        return len(rows)
    except requests.RequestException as exc:
        logger.error("Failed to write anomalies: %s", exc)
        return 0


# --------------------------------------------------------------------------- #
# Core processing (uses injected session — callable from endpoints & run())
# --------------------------------------------------------------------------- #

def _detect_anomalies(
    db: Session,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> tuple[List[ServerAnomalyResponse], int]:
    """
    Detect score anomalies across all servers.
    Returns (anomaly_responses, total_servers_count).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # Latest model_version
    latest_version = db.query(func.max(McpLlmAxisScore.model_version)).scalar()
    if not latest_version:
        return [], 0

    # Per-axis population stats
    axis_names = [
        r[0] for r in
        db.query(McpLlmAxisScore.axis_name)
        .filter(McpLlmAxisScore.model_version == latest_version)
        .distinct()
        .all()
    ]
    axis_stats: dict[str, tuple[float, float]] = {}
    for ax in axis_names:
        axis_stats[ax] = _population_stats(db, ax, latest_version, cutoff)

    # All servers
    all_servers = db.query(McpServerRegistry).all()
    total_servers = len(all_servers)

    anomaly_responses: List[ServerAnomalyResponse] = []

    for server in all_servers:
        rows = (
            db.query(McpLlmAxisScore)
            .filter(
                McpLlmAxisScore.server_id == server.server_id,
                McpLlmAxisScore.model_version == latest_version,
            )
            .all()
        )
        if not rows:
            continue

        axes: List[AxisAnomalyScore] = []
        for row in rows:
            baseline_mean, baseline_std = axis_stats.get(
                row.axis_name, (0.0, 0.0)
            )
            axes.append(_build_axis_anomaly(row, baseline_mean, baseline_std, z_threshold))

        anomaly_count = sum(1 for a in axes if a.is_anomalous)
        if anomaly_count > 0:
            anomaly_responses.append(ServerAnomalyResponse(
                server_id=server.server_id,
                server_name=server.name,
                url=server.url,
                risk_tier=server.risk_tier,
                anomaly_count=anomaly_count,
                total_axes=len(axes),
                axes=axes,
                model_version=latest_version,
                computed_at=datetime.now(timezone.utc),
            ))

    anomaly_responses.sort(key=lambda s: s.anomaly_count, reverse=True)
    return anomaly_responses, total_servers


def process_anomalies(
    db: Session,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> TriggerResponse:
    """
    Detect and write anomalies to write_service.
    Returns counts for display.
    """
    anomaly_responses, total = _detect_anomalies(db, window_hours, z_threshold)
    written = 0
    for srv_resp in anomaly_responses:
        rows = [
            {
                "server_id": srv_resp.server_id,
                "server_name": srv_resp.server_name,
                "risk_tier": srv_resp.risk_tier,
                "axis_name": ax.axis_name,
                "label": ax.label,
                "p_top": ax.p_top,
                "baseline_mean": ax.baseline_mean,
                "baseline_std": ax.baseline_std,
                "z_score": ax.z_score,
                "deviation_pct": ax.deviation_pct,
                "is_anomalous": ax.is_anomalous,
                "anomaly_direction": ax.anomaly_direction,
                "model_version": srv_resp.model_version,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            for ax in srv_resp.axes
            if ax.is_anomalous
        ]
        written += _write_anomaly_rows(rows)

    return TriggerResponse(
        processed=total,
        anomalies_found=sum(s.anomaly_count for s in anomaly_responses),
        written=written,
        failed=len(anomaly_responses) - written if written > 0 else 0,
    )


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(
        status="healthy",
        service="score_anomaly_detection_consumer",
        last_heartbeat=_last_heartbeat.get("value"),
        last_run=_last_run.get("value"),
    )


@router.get(
    "/servers/{server_id}",
    response_model=ServerAnomalyResponse,
    responses={404: {"description": "Server not found"}},
)
def get_server_anomalies(
    server_id: str,
    window_hours: int = Query(default=DEFAULT_WINDOW_HOURS, ge=1, le=720),
    z_threshold: float = Query(default=DEFAULT_Z_THRESHOLD, ge=0.0, le=10.0),
    db: Session = Depends(get_session),
) -> ServerAnomalyResponse:
    """
    Return per-axis anomaly scores for a single server.

    Each axis's p_top is compared against the population mean and standard
    deviation for that axis (all servers, latest model_version, within the
    time window). Axes with |z| > ``z_threshold`` are flagged as anomalous.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    latest_version = db.query(func.max(McpLlmAxisScore.model_version)).scalar()
    if not latest_version:
        raise HTTPException(status_code=404, detail="No scores in registry")

    axis_names = [
        r[0] for r in
        db.query(McpLlmAxisScore.axis_name)
        .filter(McpLlmAxisScore.model_version == latest_version)
        .distinct()
        .all()
    ]
    axis_stats = {}
    for ax in axis_names:
        axis_stats[ax] = _population_stats(db, ax, latest_version, cutoff)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == latest_version,
        )
        .all()
    )

    axes: List[AxisAnomalyScore] = []
    for row in rows:
        b_mean, b_std = axis_stats.get(row.axis_name, (0.0, 0.0))
        axes.append(_build_axis_anomaly(row, b_mean, b_std, z_threshold))

    anomaly_count = sum(1 for a in axes if a.is_anomalous)
    return ServerAnomalyResponse(
        server_id=server_id,
        server_name=srv.name,
        url=srv.url,
        risk_tier=srv.risk_tier,
        anomaly_count=anomaly_count,
        total_axes=len(axes),
        axes=axes,
        model_version=latest_version,
        computed_at=datetime.now(timezone.utc),
    )


@router.get(
    "/anomalies",
    response_model=AnomalyListResponse,
)
def list_anomalies(
    window_hours: int = Query(default=DEFAULT_WINDOW_HOURS, ge=1, le=720),
    z_threshold: float = Query(default=DEFAULT_Z_THRESHOLD, ge=0.0, le=10.0),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> AnomalyListResponse:
    """
    Return all servers with at least one anomalous axis score.

    Results are ordered by anomaly_count descending, capped at ``limit``.
    """
    anomaly_responses, total = _detect_anomalies(
        db, window_hours, z_threshold
    )
    return AnomalyListResponse(
        total_servers=total,
        anomalous_servers=len(anomaly_responses),
        anomaly_servers=anomaly_responses[:limit],
        computed_at=datetime.now(timezone.utc),
    )


@router.post("/trigger", response_model=TriggerResponse)
def trigger(
    window_hours: int = Query(default=DEFAULT_WINDOW_HOURS, ge=1, le=720),
    z_threshold: float = Query(default=DEFAULT_Z_THRESHOLD, ge=0.0, le=10.0),
    db: Session = Depends(get_session),
) -> TriggerResponse:
    """
    Manually trigger one-shot anomaly detection.

    Detects anomalies across all servers, computes z-scores per axis, and
    writes anomalous rows to ``mcp_score_anomalies`` via write_service.
    """
    return process_anomalies(db, window_hours, z_threshold)


# --------------------------------------------------------------------------- #
# Daemon entrypoint
# --------------------------------------------------------------------------- #

def _heartbeat_loop() -> None:
    while True:
        _send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    """
    Background daemon: start heartbeat thread, then enter processing loop.
    Fires heartbeat every HEARTBEAT_INTERVAL seconds regardless of
    work-cycle outcome.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info(
        "Starting score_anomaly_detection_consumer "
        "(write_service=%s, poll_interval=%ds, heartbeat=%ds)",
        WRITE_SERVICE_URL,
        PROCESSING_INTERVAL,
        HEARTBEAT_INTERVAL,
    )

    hb = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb.start()

    consecutive_failures = 0
    max_consecutive_failures = 5

    while True:
        cycle_start = time.time()
        _last_run["value"] = datetime.now(timezone.utc).isoformat()

        try:
            from app.db import get_session as _gs
            with contextmanager(_gs)() as _db:
                result = process_anomalies(_db)
            logger.info(
                "Cycle complete: processed=%d, anomalies=%d, written=%d, failed=%d",
                result.processed,
                result.anomalies_found,
                result.written,
                result.failed,
            )
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            logger.error(
                "Cycle failed (%d/%d): %s",
                consecutive_failures,
                max_consecutive_failures,
                exc,
            )
            if consecutive_failures >= max_consecutive_failures:
                logger.critical("Max consecutive failures reached — exiting")
                break

        elapsed = time.time() - cycle_start
        sleep_time = max(1.0, PROCESSING_INTERVAL - elapsed)
        logger.debug("Cycle %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=_engine)
    _TestSessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    @contextmanager
    def _override_session() -> Generator[Session, None, None]:
        _sess = _TestSessionLocal()
        try:
            yield _sess
        finally:
            _sess.close()

    _test_app = FastAPI()
    _test_app.include_router(router)
    _test_app.dependency_overrides[get_session] = _override_session

    _client = TestClient(_test_app)
    _now = datetime.now(timezone.utc)
    _pk = [0]

    def _nid():
        v = _pk[0]
        _pk[0] += 1
        return v

    # Seed test data
    with _TestSessionLocal() as _sess:
        _sess.add_all([
            McpServerRegistry(
                server_id="srv-normal",
                name="Normal Server",
                registry_source="self-test",
                url="http://normal.example.com",
                first_seen=_now,
                last_seen=_now,
                last_scanned=_now,
                last_assessed=_now,
                risk_tier="low",
                trust_score=0.9,
                verdict="approved",
                scan_count=1,
                confidence=0.9,
                meta=None,
            ),
            McpServerRegistry(
                server_id="srv-anomaly",
                name="Anomalous Server",
                registry_source="self-test",
                url="http://anomaly.example.com",
                first_seen=_now,
                last_seen=_now,
                last_scanned=_now,
                last_assessed=_now,
                risk_tier="high",
                trust_score=0.2,
                verdict="flagged",
                scan_count=1,
                confidence=0.5,
                meta=None,
            ),
            McpServerRegistry(
                server_id="srv-empty",
                name="Empty Server",
                registry_source="self-test",
                url="http://empty.example.com",
                first_seen=_now,
                last_seen=_now,
                last_scanned=_now,
                last_assessed=_now,
                risk_tier="unknown",
                trust_score=0.0,
                verdict="unknown",
                scan_count=0,
                confidence=0.0,
                meta=None,
            ),
        ])

        _axis_names = ["overall_risk", "auth_strength", "data_sensitivity"]
        _normal_ptop = 50.0
        _outlier_ptop = 95.0

        # Populate 4 filler servers at p_top=50 so population mean≈50, stddev>0
        for j in range(4):
            for ax in _axis_names:
                _sess.add(McpLlmAxisScore(
                    id=_nid(),
                    server_id=f"srv-fill-{j}",
                    axis_name=ax,
                    model_version="v1",
                    decision_rule_version="r1",
                    adapter_sha256="deadbeef",
                    label="medium",
                    label_index=1,
                    probs={},
                    p_critical=0.05,
                    p_danger=0.1,
                    p_top=_normal_ptop,
                    escalated=False,
                    escalated_to=None,
                    scored_at=_now,
                ))

        # srv-normal at p_top=50 (normal, z≈0)
        for ax in _axis_names:
            _sess.add(McpLlmAxisScore(
                id=_nid(),
                server_id="srv-normal",
                axis_name=ax,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="deadbeef",
                label="medium",
                label_index=1,
                probs={},
                p_critical=0.05,
                p_danger=0.1,
                p_top=_normal_ptop,
                escalated=False,
                escalated_to=None,
                scored_at=_now,
            ))

        # srv-anomaly at p_top=95 (outlier, z > 2 for any reasonable stddev)
        for ax in _axis_names:
            _sess.add(McpLlmAxisScore(
                id=_nid(),
                server_id="srv-anomaly",
                axis_name=ax,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="deadbeef",
                label="high",
                label_index=3,
                probs={},
                p_critical=0.3,
                p_danger=0.4,
                p_top=_outlier_ptop,
                escalated=False,
                escalated_to=None,
                scored_at=_now,
            ))

        _sess.commit()

    _all_passed = True

    # T1: health
    _r = _client.get("/api/score_anomaly_detection_consumer/health")
    if _r.status_code != 200:
        print(f"FAIL T1 health: HTTP {_r.status_code}")
        _all_passed = False
    elif _r.json().get("status") != "healthy":
        print(f"FAIL T1 health body: {_r.json()}")
        _all_passed = False
    else:
        print("  PASS /health")

    # T2: srv-normal — no anomalies (p_top=50, population mean≈50, z≈0)
    _r = _client.get(
        "/api/score_anomaly_detection_consumer/servers/srv-normal"
    )
    if _r.status_code != 200:
        print(f"FAIL T2 srv-normal: HTTP {_r.status_code}: {_r.text}")
        _all_passed = False
    else:
        _d = _r.json()
        if _d["anomaly_count"] != 0:
            print(f"FAIL T2: expected anomaly_count=0 for srv-normal, got {_d['anomaly_count']}")
            _all_passed = False
        elif _d["total_axes"] != 3:
            print(f"FAIL T2: expected 3 axes, got {_d['total_axes']}")
            _all_passed = False
        else:
            print(f"  PASS /servers/srv-normal → anomaly_count={_d['anomaly_count']}")

    # T3: srv-anomaly — 3 anomalies (z > 2 for all axes)
    _r = _client.get(
        "/api/score_anomaly_detection_consumer/servers/srv-anomaly"
    )
    if _r.status_code != 200:
        print(f"FAIL T3 srv-anomaly: HTTP {_r.status_code}: {_r.text}")
        _all_passed = False
    else:
        _d = _r.json()
        if _d["anomaly_count"] != 3:
            print(f"FAIL T3: expected anomaly_count=3 for srv-anomaly, got {_d['anomaly_count']}")
            _all_passed = False
        else:
            print(f"  PASS /servers/srv-anomaly → anomaly_count={_d['anomaly_count']}")
            for _ax in _d["axes"]:
                if not _ax["is_anomalous"]:
                    print(f"FAIL T3: axis {_ax['axis_name']} should be anomalous")
                    _all_passed = False
                    break

    # T4: srv-empty — 404 (no scores)
    _r = _client.get(
        "/api/score_anomaly_detection_consumer/servers/srv-empty"
    )
    if _r.status_code != 404:
        print(f"FAIL T4: expected 404 for srv-empty, got {_r.status_code}")
        _all_passed = False
    else:
        print("  PASS /servers/srv-empty → 404")

    # T5: unknown server → 404
    _r = _client.get(
        "/api/score_anomaly_detection_consumer/servers/nonexistent"
    )
    if _r.status_code != 404:
        print(f"FAIL T5: expected 404, got {_r.status_code}")
        _all_passed = False
    else:
        print("  PASS /servers/nonexistent → 404")

    # T6: anomalies list — includes srv-anomaly
    _r = _client.get(
        "/api/score_anomaly_detection_consumer/anomalies"
        "?z_threshold=2.0&limit=10"
    )
    if _r.status_code != 200:
        print(f"FAIL T6 anomalies list: HTTP {_r.status_code}: {_r.text}")
        _all_passed = False
    else:
        _d = _r.json()
        if _d["total_servers"] < 1:
            print(f"FAIL T6: total_servers should be >= 1, got {_d['total_servers']}")
            _all_passed = False
        elif len(_d["anomaly_servers"]) == 0:
            print("FAIL T6: expected anomalous servers in list")
            _all_passed = False
        elif _d["anomaly_servers"][0]["server_id"] != "srv-anomaly":
            print(f"FAIL T6: top anomalous server should be srv-anomaly, got {_d['anomaly_servers'][0]['server_id']}")
            _all_passed = False
        else:
            print(f"  PASS /anomalies → {len(_d['anomaly_servers'])} anomalous server(s)")

    # T7: trigger endpoint (write_service unavailable → caught, returns failed count)
    _r = _client.post(
        "/api/score_anomaly_detection_consumer/trigger"
    )
    if _r.status_code != 200:
        print(f"FAIL T7 trigger: HTTP {_r.status_code}: {_r.text}")
        _all_passed = False
    else:
        _d = _r.json()
        if _d.get("processed", -1) < 0:
            print(f"FAIL T7: missing 'processed' in trigger response")
            _all_passed = False
        elif _d.get("anomalies_found", -1) < 0:
            print(f"FAIL T7: missing 'anomalies_found' in trigger response")
            _all_passed = False
        else:
            print(f"  PASS /trigger → processed={_d['processed']}, anomalies={_d['anomalies_found']}")

    # T8: Pydantic shape validation — axis fields present
    _r = _client.get("/api/score_anomaly_detection_consumer/servers/srv-anomaly")
    if _r.status_code == 200:
        _d = _r.json()
        for _ax in _d["axes"]:
            if "z_score" not in _ax or "is_anomalous" not in _ax:
                print(f"FAIL T8: missing required axis field in {_ax}")
                _all_passed = False
                break
        else:
            print("  PASS Pydantic shape validation")

    if _all_passed:
        print("\nPASS")
        _sys.exit(0)
    else:
        print("\nFAIL")
        _sys.exit(1)
