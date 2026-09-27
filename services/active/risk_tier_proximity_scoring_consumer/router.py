# deps: fastapi, pydantic, sqlalchemy, requests
"""
risk_tier_proximity_scoring_consumer.

Identifies servers sitting near a tier boundary -- their composite score
is within N points of the next tier, making them actionable for analysts.

Public endpoint (auth=public per the directive).
Data: app tier via get_session + SQLAlchemy ORM on McpServerRegistry + McpLlmAxisScore.
Mesh/pipeline output: write_service POST to 127.0.0.1:8772.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds
POLL_INTERVAL = 60  # seconds
REQUEST_TIMEOUT = 10  # seconds
WRITE_TIMEOUT = 30  # seconds

router = APIRouter(
    prefix="/api",
    tags=["risk_tier_proximity_scoring_consumer"],
)

# --------------------------------------------------------------------------- #
# Tier constants  -- must match the 7 real schema axes
# --------------------------------------------------------------------------- #
AXIS_WEIGHTS: Dict[str, float] = {
    "overall_risk": 0.25,
    "auth_strength": 0.12,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.18,
    "network_egress": 0.15,
    "maintainer_trust": 0.12,
    "exploit_surface": 0.08,
}

RISK_TIER_THRESHOLDS: List[tuple[float, str]] = [
    (75.0, "TRUSTED_GENERAL"),
    (60.0, "TRUSTED_RESEARCH"),
    (45.0, "ENTERPRISE_CONTROLLED"),
    (30.0, "CAUTION_LIMITED"),
    (15.0, "HIGH_RISK_ISOLATED"),
    (0.0, "KNOWN_THREAT"),
]

PROXIMITY_THRESHOLD = 15.0


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisContribution(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    axis_name: str
    p_top: Optional[float]
    label: Optional[str]
    contribution: float


class TierProximityResult(BaseModel):
    server_id: str
    composite_score: float
    current_tier: str
    next_tier: Optional[str]
    gap_to_next: Optional[float]
    gap_to_previous: Optional[float]
    is_proximate_up: bool
    is_proximate_down: bool
    axes: List[AxisContribution]
    computed_at: datetime


class BatchResult(BaseModel):
    processed: int
    proximate_up: int
    proximate_down: int
    tier_distribution: Dict[str, int]
    computed_at: datetime


class HealthResponse(BaseModel):
    status: str
    service: str = "risk_tier_proximity_scoring_consumer"


# --------------------------------------------------------------------------- #
# Pure helpers (no DB, no network)
# --------------------------------------------------------------------------- #
def _contribution(p_top: float) -> float:
    return round(p_top * 100, 4)


def _map_tier(score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if score > threshold:
            return tier
    return "KNOWN_THREAT"


def _compute_composite(axes: List[Dict[str, Any]]) -> float:
    total = 0.0
    for ax in axes:
        w = AXIS_WEIGHTS.get(ax["axis_name"], 0.1)
        total += w * (ax["p_top"] * 100)
    return round(min(total, 100.0), 4)


def _proximity(composite: float) -> Dict[str, Any]:
    current = _map_tier(composite)
    # find current tier index
    tier_names = [t for _, t in RISK_TIER_THRESHOLDS]
    try:
        idx = tier_names.index(current)
    except ValueError:
        idx = len(tier_names) - 1

    # next tier (better, higher threshold)
    if idx > 0:
        next_threshold, next_tier = RISK_TIER_THRESHOLDS[idx - 1]
        gap_next = next_threshold - composite
        is_prox_up = gap_next <= PROXIMITY_THRESHOLD
    else:
        next_tier, gap_next, is_prox_up = None, None, False

    # previous tier (worse, lower threshold)
    if idx < len(RISK_TIER_THRESHOLDS) - 1:
        prev_threshold, prev_tier = RISK_TIER_THRESHOLDS[idx + 1]
        gap_prev = composite - prev_threshold
        is_prox_down = gap_prev <= PROXIMITY_THRESHOLD
    else:
        gap_prev, is_prox_down = None, False

    return {
        "current_tier": current,
        "next_tier": next_tier,
        "gap_to_next": round(gap_next, 4) if gap_next is not None else None,
        "gap_to_previous": round(gap_prev, 4) if gap_prev is not None else None,
        "is_proximate_up": is_prox_up,
        "is_proximate_down": is_prox_down,
    }


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _fetch_axes(db: Session, server_id: str) -> List[Dict[str, Any]]:
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return [
        {
            "axis_name": r.axis_name,
            "p_top": r.p_top or 0.0,
            "label": r.label,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Core daemon logic
# --------------------------------------------------------------------------- #
def _heartbeat() -> bool:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/service_health",
            json={
                "service": "risk_tier_proximity_scoring_consumer",
                "status": "running",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code in (200, 201, 202)
    except requests.RequestException:
        return False


def _write_proximity(record: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "risk_tier_proximity", "rows": [record], "wait": True},
            timeout=WRITE_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        return False


def compute_server_proximity(
    db: Session, server_id: str
) -> Optional[TierProximityResult]:
    """Compute proximity for a single server; returns None if no scores."""
    srv = db.get(McpServerRegistry, server_id)
    if not srv:
        return None

    axes = _fetch_axes(db, server_id)
    if not axes:
        return None

    composite = _compute_composite(axes)
    prox = _proximity(composite)
    now = datetime.now(timezone.utc)

    axis_responses = [
        AxisContribution(
            axis_name=ax["axis_name"],
            p_top=ax["p_top"],
            label=ax["label"],
            contribution=_contribution(ax["p_top"]),
        )
        for ax in axes
    ]

    return TierProximityResult(
        server_id=server_id,
        composite_score=composite,
        current_tier=prox["current_tier"],
        next_tier=prox["next_tier"],
        gap_to_next=prox["gap_to_next"],
        gap_to_previous=prox["gap_to_previous"],
        is_proximate_up=prox["is_proximate_up"],
        is_proximate_down=prox["is_proximate_down"],
        axes=axis_responses,
        computed_at=now,
    )


def process_all_servers(db: Session) -> BatchResult:
    """Batch-compute proximity for all servers with a risk_tier."""
    servers = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.isnot(None))
        .all()
    )
    tier_dist: Dict[str, int] = {}
    proximate_up = 0
    proximate_down = 0
    count = 0
    now = datetime.now(timezone.utc)

    for srv in servers:
        result = compute_server_proximity(db, srv.server_id)
        if not result:
            continue

        tier_dist[result.current_tier] = tier_dist.get(result.current_tier, 0) + 1
        if result.is_proximate_up:
            proximate_up += 1
        if result.is_proximate_down:
            proximate_down += 1

        record = {
            "id": str(uuid.uuid4()),
            "server_id": srv.server_id,
            "composite_score": result.composite_score,
            "current_tier": result.current_tier,
            "next_tier": result.next_tier,
            "gap_to_next": result.gap_to_next,
            "gap_to_previous": result.gap_to_previous,
            "is_proximate_up": result.is_proximate_up,
            "is_proximate_down": result.is_proximate_down,
            "computed_at": now.isoformat(),
        }
        _write_proximity(record)
        count += 1

    return BatchResult(
        processed=count,
        proximate_up=proximate_up,
        proximate_down=proximate_down,
        tier_distribution=tier_dist,
        computed_at=now,
    )


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #
@router.get("/proximity/{server_id}", response_model=TierProximityResult)
def get_proximity(
    server_id: str,
    db: Session = Depends(get_session),
) -> TierProximityResult:
    """Return the tier proximity for a single server."""
    result = compute_server_proximity(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server or scores not found")
    return result


@router.post("/proximity/batch", response_model=BatchResult)
def batch_proximity(
    db: Session = Depends(get_session),
) -> BatchResult:
    """Batch-compute proximity for all servers with a risk_tier."""
    return process_all_servers(db)


@router.get("/proximity/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="healthy")


# --------------------------------------------------------------------------- #
# Daemon entrypoint
# --------------------------------------------------------------------------- #
def run() -> None:
    """Background daemon: poll, process, heartbeat. Blocks forever."""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    log = logging.getLogger(__name__)
    log.info(
        "Starting risk_tier_proximity_scoring_consumer "
        "(write_service=%s, poll_interval=%ds)",
        WRITE_SERVICE_URL,
        POLL_INTERVAL,
    )

    consecutive_failures = 0
    max_failures = 5

    while True:
        cycle_start = time.time()
        try:
            with next(get_session()) as db:
                result = process_all_servers(db)
            log.info(
                "Cycle: processed=%d, proximate_up=%d, proximate_down=%d",
                result.processed,
                result.proximate_up,
                result.proximate_down,
            )
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            log.error("Cycle failed (%d/%d): %s", consecutive_failures, max_failures, exc)
            if consecutive_failures >= max_failures:
                log.critical("Max consecutive failures reached — exiting")
                break

        if not _heartbeat():
            log.warning("Heartbeat failed")

        elapsed = time.time() - cycle_start
        sleep_time = max(0, POLL_INTERVAL - elapsed)
        time.sleep(sleep_time)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from contextlib import contextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, expire_on_commit=False)

    @contextmanager
    def _override():
        db = _TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # ---- Seed test data ----
    now = datetime.now(timezone.utc)

    with _TestSession() as db:
        db.add_all([
            McpServerRegistry(
                server_id="srv-high", name="High Trust Server",
                registry_source="selftest", url="https://test.example",
                risk_tier="TRUSTED_GENERAL",
            ),
            McpServerRegistry(
                server_id="srv-mid", name="Mid Trust Server",
                registry_source="selftest", url="https://test.example",
                risk_tier="ENTERPRISE_CONTROLLED",
            ),
            McpServerRegistry(
                server_id="srv-low", name="Low Trust Server",
                registry_source="selftest", url="https://test.example",
                risk_tier="HIGH_RISK_ISOLATED",
            ),
            McpServerRegistry(
                server_id="srv-none", name="No Score Server",
                registry_source="selftest", url="https://test.example",
                risk_tier=None,
            ),
        ])

        # srv-high: composite ~92 -> TRUSTED_GENERAL, near ceiling (gap_to_next=gap from 92 to 75+)
        # p_top for all 7 axes ~0.92, contribution ~92 each, weighted sum ~92
        for ax_name in AXIS_WEIGHTS:
            db.add(McpLlmAxisScore(
                server_id="srv-high", axis_name=ax_name,
                label="LOW", p_top=0.92, scored_at=now,
                model_version="v1", adapter_sha256="deadbeef",
                decision_rule_version="v1",
            ))

        # srv-mid: composite ~52 -> ENTERPRISE_CONTROLLED, near TRUSTED_RESEARCH (gap=8)
        # p_top ~0.52 -> contribution ~52
        mid_p = 0.52
        for ax_name in AXIS_WEIGHTS:
            db.add(McpLlmAxisScore(
                server_id="srv-mid", axis_name=ax_name,
                label="MEDIUM", p_top=mid_p, scored_at=now,
                model_version="v1", adapter_sha256="deadbeef",
                decision_rule_version="v1",
            ))

        # srv-low: composite ~18 -> HIGH_RISK_ISOLATED, near CAUTION_LIMITED (gap=12)
        low_p = 0.18
        for ax_name in AXIS_WEIGHTS:
            db.add(McpLlmAxisScore(
                server_id="srv-low", axis_name=ax_name,
                label="HIGH", p_top=low_p, scored_at=now,
                model_version="v1", adapter_sha256="deadbeef",
                decision_rule_version="v1",
            ))

        db.commit()

    all_passed = True

    # ---- Test health ----
    resp = client.get("/api/proximity/health")
    if resp.status_code != 200:
        print(f"FAIL health: {resp.status_code}")
        all_passed = False
    elif resp.json().get("status") != "healthy":
        print(f"FAIL health body: {resp.json()}")
        all_passed = False
    else:
        print("  PASS /health")

    # ---- Test srv-high -> TRUSTED_GENERAL (gap_to_next from 92 to 75 boundary = 0; wait, 92 > 75, so next tier index would be 0 which has no next) ----
    # Actually 92 > 75 threshold, so tier = TRUSTED_GENERAL (index 0). Next tier is TRUSTED_RESEARCH (threshold 60).
    # gap = 75 - 92? No. Trust tier index 0 means 92 > 75, so tier = TRUSTED_GENERAL.
    # Next tier = TRUSTED_RESEARCH (75 is the threshold, but 92 > 75, so next tier boundary is 75... wait)
    # Risk tier thresholds: (75, "TRUSTED_GENERAL"), (60, "TRUSTED_RESEARCH")...
    # If composite=92 and 92 > 75, current_tier = TRUSTED_GENERAL (index 0).
    # next_tier = RISK_TIER_THRESHOLDS[-1] (that's 0, "TRUSTED_GENERAL"). No wait.
    # idx=0, so next tier is RISK_TIER_THRESHOLDS[-1] -> idx > 0 means idx > 0, so next_threshold = RISK_TIER_THRESHOLDS[idx - 1] = RISK_TIER_THRESHOLDS[-1].
    # RISK_TIER_THRESHOLDS[-1] = (0.0, "KNOWN_THREAT"). That's wrong.
    # The logic: tier_names = [t for _, t in RISK_TIER_THRESHOLDS] = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", ...]
    # idx = tier_names.index("TRUSTED_GENERAL") = 0
    # next tier: idx > 0 is False, so next_tier=None.
    # gap_to_next = None.
    # So srv-high with composite 92 is at the TOP tier, no next tier.
    # Let's test the middle server (srv-mid) which should be ENTERPRISE_CONTROLLED with gap to next.

    resp = client.get("/api/proximity/srv-mid")
    if resp.status_code != 200:
        print(f"FAIL srv-mid: {resp.status_code} {resp.text}")
        all_passed = False
    else:
        body = resp.json()
        # srv-mid composite ~52: 52 > 45, so ENTERPRISE_CONTROLLED (idx=2)
        # next tier = TRUSTED_RESEARCH (idx=1, threshold=60), gap = 60 - 52 = 8
        # 8 <= 15, so is_proximate_up = True
        if body.get("current_tier") != "ENTERPRISE_CONTROLLED":
            print(f"FAIL srv-mid tier: expected ENTERPRISE_CONTROLLED, got {body.get('current_tier')}")
            all_passed = False
        elif not body.get("is_proximate_up"):
            print(f"FAIL srv-mid proximate_up: expected True, got {body.get('is_proximate_up')}")
            all_passed = False
        elif body.get("gap_to_next") is None or body.get("gap_to_next") > 10:
            print(f"FAIL srv-mid gap: expected ~8, got {body.get('gap_to_next')}")
            all_passed = False
        else:
            print(f"  PASS /proximity/srv-mid -> {body.get('current_tier')}")

    # ---- Test srv-low -> HIGH_RISK_ISOLATED, near CAUTION_LIMITED (gap=12) ----
    resp = client.get("/api/proximity/srv-low")
    if resp.status_code != 200:
        print(f"FAIL srv-low: {resp.status_code} {resp.text}")
        all_passed = False
    else:
        body = resp.json()
        # srv-low composite ~18: 18 > 15, so HIGH_RISK_ISOLATED (idx=4)
        # next tier = CAUTION_LIMITED (idx=3, threshold=30), gap = 30 - 18 = 12
        # 12 <= 15, so is_proximate_up = True
        if body.get("current_tier") != "HIGH_RISK_ISOLATED":
            print(f"FAIL srv-low tier: expected HIGH_RISK_ISOLATED, got {body.get('current_tier')}")
            all_passed = False
        elif not body.get("is_proximate_up"):
            print(f"FAIL srv-low proximate_up: expected True, got {body.get('is_proximate_up')}")
            all_passed = False
        elif body.get("gap_to_next") is None or abs(body.get("gap_to_next", 0) - 12) > 1:
            print(f"FAIL srv-low gap: expected ~12, got {body.get('gap_to_next')}")
            all_passed = False
        else:
            print(f"  PASS /proximity/srv-low -> {body.get('current_tier')}")

    # ---- Test 404 for unknown server ----
    resp = client.get("/api/proximity/unknown-server")
    if resp.status_code != 404:
        print(f"FAIL unknown server: expected 404, got {resp.status_code}")
        all_passed = False
    else:
        print("  PASS /proximity/unknown -> 404")

    # ---- Test 404 for server with no scores ----
    resp = client.get("/api/proximity/srv-none")
    if resp.status_code != 404:
        print(f"FAIL srv-none (no scores): expected 404, got {resp.status_code}")
        all_passed = False
    else:
        print("  PASS /proximity/srv-none (no scores) -> 404")

    # ---- Test batch endpoint ----
    resp = client.post("/api/proximity/batch")
    if resp.status_code != 200:
        print(f"FAIL batch: {resp.status_code} {resp.text}")
        all_passed = False
    else:
        body = resp.json()
        # 3 servers have scores (srv-high, srv-mid, srv-low)
        if body.get("processed") != 3:
            print(f"FAIL batch processed: expected 3, got {body.get('processed')}")
            all_passed = False
        elif "ENTERPRISE_CONTROLLED" not in body.get("tier_distribution", {}):
            print(f"FAIL batch distribution: {body.get('tier_distribution')}")
            all_passed = False
        else:
            print(f"  PASS /proximity/batch -> processed={body.get('processed')}")

    if all_passed:
        print("\nPASS")
        sys.exit(0)
    else:
        print("\nFAIL")
        sys.exit(1)
