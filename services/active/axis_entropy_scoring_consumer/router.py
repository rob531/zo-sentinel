# deps: requests
from __future__ import annotations

import json
import math
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

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
HEARTBEAT_INTERVAL = 60
PROCESSING_CADENCE = 4 * 60 * 60

router = APIRouter(prefix="/api", tags=["axis_entropy_scoring_consumer"])

_last_heartbeat: dict = {"value": None}
_last_run: dict = {"value": None}


class AxisEntropyRecord(BaseModel):
    server_id: int
    axis_name: str
    entropy_score: float = Field(..., ge=0.0, description="Shannon entropy in bits")
    stability: str = Field(..., description="low|medium|high based on entropy thresholds")
    p_top: float = Field(..., ge=0.0, le=1.0, description="Max probability from distribution")
    scored_at: datetime


class AxisEntropyResponse(BaseModel):
    processed: int
    entropy_records: list[AxisEntropyRecord]


class HealthResponse(BaseModel):
    status: str
    service: str
    last_heartbeat: Optional[str] = None
    last_run: Optional[str] = None


def compute_shannon_entropy(probs: list[float]) -> float:
    """Compute Shannon entropy H = -sum(p * log2(p)) for p > 0."""
    entropy = 0.0
    for p in probs:
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 6)


def determine_stability(entropy: float) -> str:
    """Classify stability based on entropy thresholds."""
    if entropy < 0.2:
        return "low"
    elif entropy < 0.5:
        return "medium"
    else:
        return "high"


def _write_entropy_rows(rows: list[dict]) -> bool:
    """Write entropy records to write_service."""
    if not rows:
        return True
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "mcp_axis_entropy", "rows": rows},
            timeout=10
        )
        return resp.status_code == 200
    except Exception:
        return False


def _send_heartbeat() -> bool:
    """Send heartbeat to service_health."""
    _last_heartbeat["value"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": [{
                    "service": "axis_entropy_scoring_consumer",
                    "status": "running",
                    "last_heartbeat": _last_heartbeat["value"],
                    "meta": {"last_run": _last_run.get("value")}
                }]
            },
            timeout=5
        )
        return resp.status_code == 200
    except Exception:
        return False


def _heartbeat_loop() -> None:
    """Background thread: sends heartbeat every HEARTBEAT_INTERVAL seconds."""
    while True:
        _send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def _process_axis_scores(session: Session) -> list[AxisEntropyRecord]:
    """Read McpLlmAxisScore rows, compute entropy, return records."""
    records: list[AxisEntropyRecord] = []

    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.probs.isnot(None))
        .all()
    )

    for score in scores:
        try:
            probs = json.loads(score.probs) if isinstance(score.probs, str) else score.probs
            if not probs or not isinstance(probs, list):
                continue
        except (json.JSONDecodeError, TypeError):
            continue

        entropy = compute_shannon_entropy(probs)
        stability = determine_stability(entropy)
        p_top = score.p_top or (max(probs) if probs else 0.0)
        scored_at = score.scored_at or datetime.now(timezone.utc)

        records.append(AxisEntropyRecord(
            server_id=score.server_id,
            axis_name=score.axis_name,
            entropy_score=entropy,
            stability=stability,
            p_top=p_top,
            scored_at=scored_at
        ))

    return records


@router.get("/axis-entropy/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Liveness probe for the axis entropy scoring consumer."""
    return HealthResponse(
        status="healthy",
        service="axis_entropy_scoring_consumer",
        last_heartbeat=_last_heartbeat.get("value"),
        last_run=_last_run.get("value")
    )


@router.post("/axis-entropy/process", response_model=AxisEntropyResponse)
def process_axis_entropy(
    db: Session = Depends(get_session)
) -> AxisEntropyResponse:
    """
    Trigger one-shot processing of axis scores.
    Reads all McpLlmAxisScore rows, computes Shannon entropy per axis,
    and writes results to mcp_axis_entropy via write_service.
    """
    records = _process_axis_scores(db)

    if records:
        rows = [
            {
                "server_id": r.server_id,
                "axis_name": r.axis_name,
                "entropy_score": r.entropy_score,
                "stability": r.stability,
                "p_top": r.p_top,
                "scored_at": r.scored_at.isoformat() if r.scored_at else None
            }
            for r in records
        ]
        _write_entropy_rows(rows)

    _last_run["value"] = datetime.now(timezone.utc).isoformat()

    return AxisEntropyResponse(
        processed=len(records),
        entropy_records=records
    )


def run() -> None:
    """
    Daemon entry point. Starts heartbeat thread and enters processing loop.
    Runs on PROCESSING_CADENCE interval.
    """
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    while True:
        start_time = time.time()
        _last_run["value"] = datetime.now(timezone.utc).isoformat()

        try:
            with get_session() as session:
                records = _process_axis_scores(session)

                if records:
                    rows = [
                        {
                            "server_id": r.server_id,
                            "axis_name": r.axis_name,
                            "entropy_score": r.entropy_score,
                            "stability": r.stability,
                            "p_top": r.p_top,
                            "scored_at": r.scored_at.isoformat() if r.scored_at else None
                        }
                        for r in records
                    ]
                    _write_entropy_rows(rows)
                    print(f"axis_entropy_scoring_consumer: wrote {len(rows)} entropy records")
                else:
                    print("axis_entropy_scoring_consumer: no axis scores to process")
        except Exception as e:
            print(f"axis_entropy_scoring_consumer: error during processing: {e}")

        elapsed = time.time() - start_time
        sleep_time = max(1, PROCESSING_CADENCE - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    # --- Shannon entropy unit tests ---
    test_cases = [
        {"probs": [0.125] * 8, "expected_entropy": 3.0},
        {"probs": [0.95, 0.016, 0.016, 0.005, 0.005, 0.001, 0.001, 0.001], "expected_entropy": 0.54},
        {"probs": [0.45, 0.45, 0.025, 0.025, 0.0125, 0.0125, 0.0125, 0.0125], "expected_entropy": 1.45},
        {"probs": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "expected_entropy": 0.0},
        {"probs": [0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "expected_entropy": 1.0},
    ]

    for tc in test_cases:
        entropy = compute_shannon_entropy(tc["probs"])
        diff = abs(entropy - tc["expected_entropy"])
        if diff > 0.01:
            print(f"FAIL: expected ~{tc['expected_entropy']}, got {entropy:.4f}")
            exit(1)

    stability_tests = [
        {"entropy": 0.1, "expected": "low"},
        {"entropy": 0.35, "expected": "medium"},
        {"entropy": 0.6, "expected": "high"},
    ]

    for tc in stability_tests:
        stability = determine_stability(tc["entropy"])
        if stability != tc["expected"]:
            print(f"FAIL: entropy {tc['entropy']} expected stability {tc['expected']}, got {stability}")
            exit(1)

    # --- FastAPI endpoint tests with SQLite override ---
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    that_app = FastAPI()

    @contextmanager
    def override_get_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    client = TestClient(that_app)

    # Health check
    response = client.get("/api/axis-entropy/health")
    if response.status_code != 200:
        print(f"FAIL: health check returned {response.status_code}")
        exit(1)
    if response.json()["status"] != "healthy":
        print("FAIL: health check status != healthy")
        exit(1)

    # Process endpoint (no data)
    response = client.post("/api/axis-entropy/process")
    if response.status_code != 200:
        print(f"FAIL: process endpoint returned {response.status_code}")
        exit(1)
    data = response.json()
    if "processed" not in data:
        print("FAIL: process response missing 'processed' field")
        exit(1)

    # Process endpoint with seeded data
    session = TestingSessionLocal()
    session.add(McpLlmAxisScore(
        id=1,
        server_id="srv-001",
        axis_name="overall_risk",
        label="critical",
        label_index=0,
        probs="[0.8,0.15,0.05,0.0,0.0,0.0,0.0,0.0]",
        p_top=0.8,
        p_critical=0.8,
        p_danger=0.15,
        model_version="v1",
        adapter_sha256="abc123",
        scored_at=datetime.now(timezone.utc)
    ))
    session.add(McpLlmAxisScore(
        id=2,
        server_id="srv-001",
        axis_name="auth_strength",
        label="elevated",
        label_index=1,
        probs="[0.1,0.3,0.6,0.0,0.0,0.0,0.0,0.0]",
        p_top=0.6,
        p_critical=0.1,
        p_danger=0.3,
        model_version="v1",
        adapter_sha256="abc123",
        scored_at=datetime.now(timezone.utc)
    ))
    session.commit()
    session.close()

    response = client.post("/api/axis-entropy/process")
    if response.status_code != 200:
        print(f"FAIL: process with data returned {response.status_code}")
        exit(1)
    data = response.json()
    if data["processed"] != 2:
        print(f"FAIL: expected processed=2, got {data['processed']}")
        exit(1)
    for rec in data["entropy_records"]:
        assert rec["entropy_score"] >= 0.0
        assert rec["stability"] in ("low", "medium", "high")
        assert 0.0 <= rec["p_top"] <= 1.0

    print("PASS")
