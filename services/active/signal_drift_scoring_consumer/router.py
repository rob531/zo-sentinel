# deps: fastapi, pydantic, sqlalchemy, requests
"""
signal_drift_scoring_consumer — FastAPI daemon.

Reads axis timeline data from mcp_llm_axis_scores (app DB, SQLAlchemy session),
computes signal drift metrics (volatility + trend direction) per axis, and writes
drift scores to the pipeline layer (mcp_signal_scores) via write_service.

Health heartbeat fires every <=60 s regardless of work-cycle outcome.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_URL = f"{WRITE_SERVICE_URL}/service_health"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
REQUEST_TIMEOUT = 10  # seconds
WRITE_TIMEOUT = 30  # seconds
HEARTBEAT_INTERVAL = 60  # seconds
POLL_INTERVAL = 60  # seconds

# Drift thresholds
VOLATILITY_THRESHOLD = 0.10
TREND_THRESHOLD = 0.05

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["signal_drift_scoring_consumer"],
)


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisDriftData(BaseModel):
    axis_name: str
    current_p_top: Optional[float]
    volatility: float
    trend_direction: float
    drift_flag: bool


class SignalDriftResponse(BaseModel):
    server_id: str
    axes: list[AxisDriftData]
    composite_volatility: float
    drift_alert: bool


class DriftSummary(BaseModel):
    server_id: str
    total_axes: int
    drifting_axes: int
    composite_volatility: float
    drift_alert: bool


class ProcessResult(BaseModel):
    processed: int
    skipped: int
    failed: int


class HealthResponse(BaseModel):
    status: str
    service: str


# --------------------------------------------------------------------------- #
# Pure computation helpers (no DB, no network)
# --------------------------------------------------------------------------- #
def _compute_volatility(p_top_values: list[float]) -> float:
    """Sample std dev of p_top values; 0.0 when < 2 samples."""
    if len(p_top_values) < 2:
        return 0.0
    n = len(p_top_values)
    mean = sum(p_top_values) / n
    variance = sum((v - mean) ** 2 for v in p_top_values) / (n - 1)
    return float(variance ** 0.5)


def _compute_trend_direction(p_top_values: list[float]) -> float:
    """Slope of p_top over time using ordinary least squares."""
    n = len(p_top_values)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = (n - 1) / 2.0
    y_mean = sum(p_top_values) / n
    dx = [xi - x_mean for xi in x]
    dy = [yi - y_mean for yi in p_top_values]
    numerator = sum(dxi * dyi for dxi, dyi in zip(dx, dy))
    denominator = sum(dxi ** 2 for dxi in dx)
    if denominator == 0:
        return 0.0
    return float(numerator / denominator)


def _is_drift(volatility: float, trend: float) -> bool:
    return volatility > VOLATILITY_THRESHOLD or abs(trend) > TREND_THRESHOLD


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _fetch_axis_timeline(
    server_id: str,
    lookback_days: int,
    db: Session,
) -> list[dict[str, Any]]:
    """Return ordered (axis_name, p_top, scored_at) rows for a server within lookback."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at)
    )
    rows = db.execute(stmt).scalars().all()
    return [
        {"axis_name": r.axis_name, "p_top": r.p_top, "scored_at": r.scored_at}
        for r in rows
        if r.p_top is not None
    ]


def _fetch_all_servers_with_scores(db: Session) -> list[str]:
    """Return distinct server_ids that have mcp_llm_axis_scores."""
    stmt = select(McpLlmAxisScore.server_id).distinct()
    return list(db.execute(stmt).scalars().all())


# --------------------------------------------------------------------------- #
# Signal drift computation (pure function)
# --------------------------------------------------------------------------- #
def compute_signal_drift(timeline: list[dict[str, Any]]) -> SignalDriftResponse:
    """Aggregate axis timeline into per-axis drift metrics and composite score."""
    axes_map: dict[str, list[float]] = {}
    last_p_top: dict[str, float] = {}
    for entry in timeline:
        ax = entry["axis_name"]
        axes_map.setdefault(ax, []).append(entry["p_top"])
        last_p_top[ax] = entry["p_top"]

    axes_results: list[AxisDriftData] = []
    all_volatilities: list[float] = []
    any_drift = False

    for axis_name, p_values in axes_map.items():
        volatility = _compute_volatility(p_values)
        trend = _compute_trend_direction(p_values)
        drift_flag = _is_drift(volatility, trend)
        all_volatilities.append(volatility)
        if drift_flag:
            any_drift = True

        axes_results.append(
            AxisDriftData(
                axis_name=axis_name,
                current_p_top=last_p_top.get(axis_name),
                volatility=round(volatility, 6),
                trend_direction=round(trend, 6),
                drift_flag=drift_flag,
            )
        )

    composite_volatility = float(sum(all_volatilities) / len(all_volatilities)) if all_volatilities else 0.0
    server_id = ""
    if timeline:
        # infer server_id from first row; endpoint provides it separately
        pass

    return SignalDriftResponse(
        server_id="",
        axes=axes_results,
        composite_volatility=round(composite_volatility, 6),
        drift_alert=any_drift,
    )


# --------------------------------------------------------------------------- #
# Pipeline write helpers (network I/O)
# --------------------------------------------------------------------------- #
def _send_heartbeat() -> bool:
    try:
        resp = requests.post(
            SERVICE_HEALTH_URL,
            json={
                "service": "signal_drift_scoring_consumer",
                "status": "running",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code in (200, 201, 202)
    except requests.RequestException as exc:
        logger.warning("Heartbeat failed: %s", exc)
        return False


def _write_drift_score(server_id: str, drift_result: SignalDriftResponse) -> bool:
    """Write drift result to mcp_signal_scores pipeline table via write_service."""
    payload = {
        "table": "mcp_signal_scores",
        "rows": [
            {
                "server_id": server_id,
                "signal_type": "signal_drift",
                "composite_volatility": drift_result.composite_volatility,
                "drift_alert": drift_result.drift_alert,
                "drifting_axes_count": sum(1 for a in drift_result.axes if a.drift_flag),
                "total_axes": len(drift_result.axes),
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "meta": {
                    "axes": [
                        {
                            "axis_name": a.axis_name,
                            "volatility": a.volatility,
                            "trend_direction": a.trend_direction,
                            "drift_flag": a.drift_flag,
                        }
                        for a in drift_result.axes
                    ]
                },
            }
        ],
        "wait": True,
    }
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=WRITE_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Failed to write drift score for %s: %s", server_id, exc)
        return False


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #
@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="healthy", service="signal_drift_scoring_consumer")


@router.get("/signal-drift/{server_id}", response_model=SignalDriftResponse)
def get_signal_drift(
    server_id: str,
    lookback_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> SignalDriftResponse:
    """Compute signal drift metrics for a single server's axis timeline."""
    timeline = _fetch_axis_timeline(server_id, lookback_days, db)
    if not timeline:
        raise HTTPException(status_code=404, detail=f"No scoring data for server {server_id}")

    result = compute_signal_drift(timeline)
    result.server_id = server_id
    return result


@router.get("/signal-drift/{server_id}/summary", response_model=DriftSummary)
def get_drift_summary(
    server_id: str,
    lookback_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> DriftSummary:
    """Lightweight drift summary for a server."""
    timeline = _fetch_axis_timeline(server_id, lookback_days, db)
    if not timeline:
        raise HTTPException(status_code=404, detail=f"No scoring data for server {server_id}")

    result = compute_signal_drift(timeline)
    return DriftSummary(
        server_id=server_id,
        total_axes=len(result.axes),
        drifting_axes=sum(1 for a in result.axes if a.drift_flag),
        composite_volatility=result.composite_volatility,
        drift_alert=result.drift_alert,
    )


@router.post("/trigger", response_model=ProcessResult)
def trigger_batch(
    lookback_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ProcessResult:
    """Process all servers with axis scores and write drift results to pipeline."""
    server_ids = _fetch_all_servers_with_scores(db)
    if not server_ids:
        return ProcessResult(processed=0, skipped=0, failed=0)

    processed, skipped, failed = 0, 0, 0
    for sid in server_ids:
        timeline = _fetch_axis_timeline(sid, lookback_days, db)
        if not timeline:
            skipped += 1
            continue
        result = compute_signal_drift(timeline)
        result.server_id = sid
        ok = _write_drift_score(sid, result)
        if ok:
            processed += 1
        else:
            failed += 1

    return ProcessResult(processed=processed, skipped=skipped, failed=failed)


# --------------------------------------------------------------------------- #
# Daemon entrypoint
# --------------------------------------------------------------------------- #
def run() -> None:
    """Background daemon: poll all servers, compute drift, write to pipeline, heartbeat."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info(
        "Starting signal_drift_scoring_consumer daemon "
        "(write_service=%s, poll_interval=%ds)",
        WRITE_SERVICE_URL,
        POLL_INTERVAL,
    )

    consecutive_failures = 0
    max_consecutive_failures = 5

    while True:
        cycle_start = time.time()

        try:
            # Use the module-level get_session for the daemon run loop
            from contextlib import closing
            with closing(next(get_session())) as db:
                server_ids = _fetch_all_servers_with_scores(db)
            logger.info("Found %d servers with scores", len(server_ids))

            for sid in server_ids:
                with closing(next(get_session())) as db:
                    timeline = _fetch_axis_timeline(sid, 30, db)
                if not timeline:
                    continue
                result = compute_signal_drift(timeline)
                result.server_id = sid
                _write_drift_score(sid, result)

            consecutive_failures = 0
            logger.info("Cycle complete: %d servers processed", len(server_ids))
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

        if not _send_heartbeat():
            logger.warning("Heartbeat failed")

        elapsed = time.time() - cycle_start
        sleep_time = max(0.0, POLL_INTERVAL - elapsed)
        logger.debug("Cycle %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from contextlib import contextmanager
    from datetime import datetime as dt, timezone as tz

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite seeded with real ORM models (no live Postgres needed)
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, expire_on_commit=False)

    # Seed test data: 3 servers x 3 axes x 5 time points
    _servers = ["srv-aaa", "srv-bbb", "srv-ccc"]
    _axes = ["overall_risk", "data_sensitivity", "exploit_surface"]
    _base_times = [
        dt(2025, 1, 1, 10, 0, 0, tzinfo=tz.utc),
        dt(2025, 1, 2, 10, 0, 0, tzinfo=tz.utc),
        dt(2025, 1, 3, 10, 0, 0, tzinfo=tz.utc),
        dt(2025, 1, 4, 10, 0, 0, tzinfo=tz.utc),
        dt(2025, 1, 5, 10, 0, 0, tzinfo=tz.utc),
    ]

    with _TestSession() as _sess:
        for srv in _servers:
            for idx, bt in enumerate(_base_times):
                for ax in _axes:
                    p_val = 0.3 + (idx * 0.12) + ((hash(f"{srv}{ax}") % 100) / 1000.0)
                    _sess.add(
                        McpLlmAxisScore(
                            server_id=srv,
                            axis_name=ax,
                            label="test",
                            label_index=0,
                            p_top=p_val,
                            p_critical=0.1 + idx * 0.02,
                            p_danger=0.2 + idx * 0.03,
                            probs={},
                            escalated=False,
                            decision_rule_version="r1",
                            model_version="v1",
                            adapter_sha256="abc123",
                            scored_at=bt,
                        )
                    )
        _sess.commit()

    # Pure-function tests
    all_passed = True

    vol_cases = [
        ([0.1, 0.2, 0.3], 0.1),
        ([0.5], 0.0),
        ([], 0.0),
        ([1.0, 1.0, 1.0], 0.0),
    ]
    for vals, expected in vol_cases:
        got = _compute_volatility(vals)
        ok = abs(got - expected) < 1e-9
        status = "PASS" if ok else f"FAIL (got {got})"
        print(f"  volatility({vals}) -> {got:.6f}  {status}")
        if not ok:
            all_passed = False

    trend_cases = [
        ([0.1, 0.2, 0.3], 0.1),
        ([0.5], 0.0),
        ([], 0.0),
        ([1.0, 1.0, 1.0], 0.0),
    ]
    for vals, expected in trend_cases:
        got = _compute_trend_direction(vals)
        ok = abs(got - expected) < 1e-9
        status = "PASS" if ok else f"FAIL (got {got})"
        print(f"  trend({vals}) -> {got:.6f}  {status}")
        if not ok:
            all_passed = False

    drift_cases = [
        (0.15, 0.01, True),   # volatility > threshold
        (0.05, 0.10, True),   # trend > threshold
        (0.05, 0.02, False),  # both below
        (0.0, 0.0, False),
    ]
    for vol, trend, expected in drift_cases:
        got = _is_drift(vol, trend)
        ok = got == expected
        status = "PASS" if ok else f"FAIL (got {got})"
        print(f"  is_drift({vol}, {trend}) -> {got}  {status}")
        if not ok:
            all_passed = False

    # FastAPI endpoint tests with dependency override
    _test_app = FastAPI()
    _test_app.include_router(router)

    @contextmanager
    def _override_session():
        s = _TestSession()
        try:
            yield s
        finally:
            s.close()

    _test_app.dependency_overrides[get_session] = _override_session
    _client = TestClient(_test_app)

    # Health
    resp = _client.get("/api/health")
    if resp.status_code != 200:
        print(f"FAIL health: HTTP {resp.status_code}")
        all_passed = False
    elif resp.json().get("status") != "healthy":
        print(f"FAIL health body: {resp.json()}")
        all_passed = False
    else:
        print("  PASS /health")

    # Signal drift endpoint
    for srv in _servers:
        resp = _client.get(f"/api/signal-drift/{srv}", params={"lookback_days": 30})
        if resp.status_code != 200:
            print(f"FAIL signal-drift/{srv}: HTTP {resp.status_code}")
            all_passed = False
            continue
        data = resp.json()
        if "axes" not in data:
            print(f"FAIL signal-drift/{srv}: missing 'axes'")
            all_passed = False
            continue
        for ax in data["axes"]:
            if not isinstance(ax.get("drift_flag"), bool):
                print(
                    f"FAIL drift_flag type for {srv}/{ax.get('axis_name')}: "
                    f"{type(ax.get('drift_flag'))}"
                )
                all_passed = False
                break
        else:
            print(f"  PASS /signal-drift/{srv} ({len(data['axes'])} axes, "
                  f"drift_alert={data.get('drift_alert')})")

    # Summary endpoint
    resp = _client.get(f"/api/signal-drift/{_servers[0]}/summary", params={"lookback_days": 30})
    if resp.status_code != 200:
        print(f"FAIL summary: HTTP {resp.status_code}")
        all_passed = False
    else:
        data = resp.json()
        required = {"server_id", "total_axes", "drifting_axes", "composite_volatility", "drift_alert"}
        if not required.issubset(data.keys()):
            print(f"FAIL summary: missing fields {required - set(data.keys())}")
            all_passed = False
        else:
            print(f"  PASS /summary -> drifting_axes={data['drifting_axes']}, "
                  f"volatility={data['composite_volatility']:.6f}")

    # Unknown server -> 404
    resp = _client.get("/api/signal-drift/no-such-server", params={"lookback_days": 30})
    if resp.status_code != 404:
        print(f"FAIL unknown server: expected 404, got {resp.status_code}")
        all_passed = False
    else:
        print("  PASS unknown server -> 404")

    if all_passed:
        print("\nPASS")
        sys.exit(0)
    else:
        print("\nFAIL")
        sys.exit(1)
