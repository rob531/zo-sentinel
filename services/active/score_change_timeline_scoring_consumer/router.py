# deps: fastapi, pydantic, sqlalchemy, requests
"""Score Change Timeline Scoring Consumer.

Detects significant axis-label transitions between consecutive McpLlmAxisScore runs
for each server and writes them to the mcp_signal_scores mesh table via write_service.

Auth:   public  (PRODUCT_SPEC §9 scope: intelligence/detection artefact only).
DB:     app tier → get_session + McpLlmAxisScore / McpServerRegistry ORM.
Mesh:   pipeline tables → POST http://127.0.0.1:8772/write  (write_service).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_change_timeline_scoring_consumer"])

SERVICE_NAME = "score_change_timeline_scoring_consumer"
HEALTH_URL = "http://127.0.0.1:8772/service_health"
WRITE_URL = "http://127.0.0.1:8772/write"

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class AxisTransitionRecord(BaseModel):
    """One axis-level label transition between two consecutive score runs."""
    axis_name: str
    old_label: str | None
    new_label: str | None
    old_label_index: int | None
    new_label_index: int | None
    delta_index: float
    direction: str  # "escalated" | "de-escalated" | "stable"

    model_config = ConfigDict(from_attributes=True)


class ServerChangeRecord(BaseModel):
    """All axis transitions for one server between two consecutive scoring runs."""
    server_id: str
    prior_scored_at: str | None
    current_scored_at: str | None
    transitions: list[AxisTransitionRecord]
    has_significant_change: bool

    model_config = ConfigDict(from_attributes=True)


class ProcessResponse(BaseModel):
    servers_scanned: int
    servers_with_changes: int
    total_transitions: int
    written: int
    elapsed_seconds: float


class ServerTimelineResponse(BaseModel):
    server_id: str
    changes: list[ServerChangeRecord]


class HealthResponse(BaseModel):
    service: str
    status: str
    last_run: str | None
    servers_scanned: int | None


# --------------------------------------------------------------------------- #
# Core computation (pure function, no I/O)
# --------------------------------------------------------------------------- #


def compute_score_changes(
    server_id: str,
    scores: list[McpLlmAxisScore],
) -> ServerChangeRecord:
    """
    Given all axis scores for one server (already ordered by scored_at ASC),
    compare consecutive pairs and emit axis transitions.

    A "significant" change is any label_index delta whose absolute value > 0.
    """
    if len(scores) < 2:
        return ServerChangeRecord(
            server_id=server_id,
            prior_scored_at=None,
            current_scored_at=None,
            transitions=[],
            has_significant_change=False,
        )

    # Group by scored_at so we compare across complete runs
    by_run: dict[datetime, dict[str, McpLlmAxisScore]] = {}
    for s in scores:
        by_run.setdefault(s.scored_at, {})[s.axis_name] = s

    sorted_times = sorted(by_run.keys())
    if len(sorted_times) < 2:
        return ServerChangeRecord(
            server_id=server_id,
            prior_scored_at=None,
            current_scored_at=None,
            transitions=[],
            has_significant_change=False,
        )

    prior_run_ts = sorted_times[-2]
    current_run_ts = sorted_times[-1]

    prior_by_axis = by_run[prior_run_ts]
    current_by_axis = by_run[current_run_ts]

    all_axes = set(prior_by_axis.keys()) | set(current_by_axis.keys())
    transitions: list[AxisTransitionRecord] = []

    for axis in sorted(all_axes):
        prior_score: McpLlmAxisScore | None = prior_by_axis.get(axis)
        current_score: McpLlmAxisScore | None = current_by_axis.get(axis)

        if prior_score is None or current_score is None:
            continue

        delta_index = (current_score.label_index or 0) - (prior_score.label_index or 0)

        if delta_index > 0:
            direction = "escalated"
        elif delta_index < 0:
            direction = "de-escalated"
        else:
            direction = "stable"

        transitions.append(
            AxisTransitionRecord(
                axis_name=axis,
                old_label=prior_score.label,
                new_label=current_score.label,
                old_label_index=prior_score.label_index,
                new_label_index=current_score.label_index,
                delta_index=round(delta_index, 6),
                direction=direction,
            )
        )

    has_sig = any(abs(t.delta_index) > 0 for t in transitions)

    return ServerChangeRecord(
        server_id=server_id,
        prior_scored_at=prior_run_ts.isoformat() if prior_run_ts else None,
        current_scored_at=current_run_ts.isoformat() if current_run_ts else None,
        transitions=transitions,
        has_significant_change=has_sig,
    )


# --------------------------------------------------------------------------- #
# Data access helpers
# --------------------------------------------------------------------------- #


def fetch_all_axis_scores(session: Session) -> dict[str, list[McpLlmAxisScore]]:
    """Return {server_id: [McpLlmAxisScore, ...]} ordered by scored_at ASC."""
    rows = (
        session.query(McpLlmAxisScore)
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at)
        .all()
    )
    grouped: dict[str, list[McpLlmAxisScore]] = {}
    for row in rows:
        grouped.setdefault(row.server_id, []).append(row)
    return grouped


def fetch_server_timeline(
    session: Session,
    server_id: str,
) -> ServerChangeRecord:
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )
    return compute_score_changes(server_id, rows)


# --------------------------------------------------------------------------- #
# Mesh write helper
# --------------------------------------------------------------------------- #


def write_transition_record(record: ServerChangeRecord) -> bool:
    """
    POST one server's change record to the mcp_signal_scores mesh table
    via write_service.  Silently swallows network errors (heartbeat still fires).
    """
    try:
        resp = requests.post(
            WRITE_URL,
            json={
                "table": "mcp_signal_scores",
                "rows": {
                    "server_id": record.server_id,
                    "signal_type": "score_change_timeline",
                    "prior_scored_at": record.prior_scored_at,
                    "current_scored_at": record.current_scored_at,
                    "transitions_json": json.dumps(
                        [t.model_dump() for t in record.transitions]
                    ),
                    "has_significant_change": record.has_significant_change,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                "wait": True,
            },
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except requests.RequestException:
        return False


# --------------------------------------------------------------------------- #
# Daemon cycle
# --------------------------------------------------------------------------- #


def run_cycle(session: Session) -> tuple[int, int, int]:
    """
    Scan all servers, detect score transitions, write to mesh.
    Returns (servers_scanned, servers_with_changes, written).
    """
    by_server = fetch_all_axis_scores(session)
    written = 0
    servers_with_changes = 0

    for server_id, scores in by_server.items():
        change = compute_score_changes(server_id, scores)
        if change.has_significant_change:
            servers_with_changes += 1
            if write_transition_record(change):
                written += 1

    return len(by_server), servers_with_changes, written


def send_heartbeat(healthy: bool = True) -> None:
    try:
        requests.post(
            HEALTH_URL,
            json={
                "service": SERVICE_NAME,
                "status": "healthy" if healthy else "degraded",
                "meta": {"last_run": datetime.now(timezone.utc).isoformat()},
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


# --------------------------------------------------------------------------- #
# FastAPI endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/scoring/score-change-timeline/process",
    response_model=ProcessResponse,
    summary="Run the score-change-timeline scoring cycle",
)
async def process_score_changes(
    session: Session = Depends(get_session),
) -> ProcessResponse:
    """
    Trigger one scoring cycle: scan servers for axis label transitions
    and write significant changes to the mcp_signal_scores mesh table.
    """
    start = time.monotonic()
    scanned, changed, written = run_cycle(session)
    elapsed = time.monotonic() - start
    send_heartbeat(healthy=True)
    return ProcessResponse(
        servers_scanned=scanned,
        servers_with_changes=changed,
        total_transitions=changed,
        written=written,
        elapsed_seconds=round(elapsed, 3),
    )


@router.get(
    "/scoring/score-change-timeline/server/{server_id}",
    response_model=ServerChangeRecord,
    summary="Get score-change timeline for one server",
)
async def get_server_timeline(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerChangeRecord:
    """Return the latest axis-label transitions for a given server."""
    return fetch_server_timeline(session, server_id)


@router.get(
    "/scoring/score-change-timeline/health",
    response_model=HealthResponse,
    summary="Service health probe",
)
async def health() -> HealthResponse:
    """Liveness probe — does NOT run the scoring cycle."""
    return HealthResponse(
        service=SERVICE_NAME,
        status="healthy",
        last_run=None,
        servers_scanned=None,
    )


# --------------------------------------------------------------------------- #
# Self-test (executed when running this file directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite for self-test (no live Postgres required)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # ── Seed test data ────────────────────────────────────────────────────────
    with TestSessionLocal() as session:
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # server-a: label_index goes 1 → 5  (escalated, |Δ|=4 > 0 → significant)
        for idx, (li, label, offset_h) in enumerate(
            [
                (1, "low", 0),
                (5, "high", 1),
            ]
        ):
            session.add(
                McpLlmAxisScore(
                    server_id="server-a",
                    axis_name="safety",
                    label_index=li,
                    label=label,
                    p_top=0.5,
                    p_critical=0.1,
                    p_danger=0.2,
                    probs={},
                    scored_at=base,
                    model_version="model_v1",
                    adapter_sha256="sha_a",
                    decision_rule_version="rule_v1",
                    escalated=False,
                    escalated_to=None,
                )
            )
            base = datetime(2024, 1, 1, 12 + offset_h, 0, 0, tzinfo=timezone.utc)

        # server-b: label_index goes 4 → 2  (de-escalated, |Δ|=2 > 0 → significant)
        base = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        for li, label, offset_h in [(4, "medium", 0), (2, "low", 1)]:
            session.add(
                McpLlmAxisScore(
                    server_id="server-b",
                    axis_name="safety",
                    label_index=li,
                    label=label,
                    p_top=0.5,
                    p_critical=0.1,
                    p_danger=0.2,
                    probs={},
                    scored_at=base,
                    model_version="model_v1",
                    adapter_sha256="sha_b",
                    decision_rule_version="rule_v1",
                    escalated=False,
                    escalated_to=None,
                )
            )
            base = datetime(2024, 1, 2, 12 + offset_h, 0, 0, tzinfo=timezone.utc)

        # server-c: label_index goes 2 → 2  (stable, |Δ|=0 → NOT significant)
        base = datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
        for li, label, offset_h in [(2, "low", 0), (2, "low", 1)]:
            session.add(
                McpLlmAxisScore(
                    server_id="server-c",
                    axis_name="safety",
                    label_index=li,
                    label=label,
                    p_top=0.5,
                    p_critical=0.1,
                    p_danger=0.2,
                    probs={},
                    scored_at=base,
                    model_version="model_v1",
                    adapter_sha256="sha_c",
                    decision_rule_version="rule_v1",
                    escalated=False,
                    escalated_to=None,
                )
            )
            base = datetime(2024, 1, 3, 12 + offset_h, 0, 0, tzinfo=timezone.utc)

        session.commit()

    # ── Mock write_service so we don't need the real mesh ────────────────────
    written_records: list[ServerChangeRecord] = []

    original_write = write_transition_record

    def mock_write(record: ServerChangeRecord) -> bool:
        written_records.append(record)
        return True

    # Patch module-level write helper for the cycle
    import services.active.score_change_timeline_scoring_consumer.router as router_module
    router_module.write_transition_record = mock_write  # type: ignore[attr-defined]

    # ── Run cycle via FastAPI endpoint ───────────────────────────────────────
    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    resp = client.get("/api/scoring/score-change-timeline/process")
    assert resp.status_code == 200, f"process endpoint failed: {resp.status_code} {resp.text}"
    result = resp.json()
    assert result["servers_scanned"] == 3, f"Expected 3 servers, got {result}"
    assert result["servers_with_changes"] == 2, f"Expected 2 servers with changes, got {result}"
    assert len(written_records) == 2, f"Expected 2 records written, got {len(written_records)}"

    # Verify server-a escalated
    rec_a = next(r for r in written_records if r.server_id == "server-a")
    assert rec_a.has_significant_change is True
    assert rec_a.transitions[0].direction == "escalated"
    assert rec_a.transitions[0].delta_index > 0

    # Verify server-b de-escalated
    rec_b = next(r for r in written_records if r.server_id == "server-b")
    assert rec_b.has_significant_change is True
    assert rec_b.transitions[0].direction == "de-escalated"
    assert rec_b.transitions[0].delta_index < 0

    # ── Test per-server timeline endpoint ────────────────────────────────────
    resp_a = client.get("/api/scoring/score-change-timeline/server/server-a")
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["server_id"] == "server-a"
    assert data_a["has_significant_change"] is True
    assert len(data_a["transitions"]) == 1

    resp_c = client.get("/api/scoring/score-change-timeline/server/server-c")
    assert resp_c.status_code == 200
    data_c = resp_c.json()
    assert data_c["server_id"] == "server-c"
    assert data_c["has_significant_change"] is False  # stable — not written

    # ── Health endpoint ──────────────────────────────────────────────────────
    resp_h = client.get("/api/scoring/score-change-timeline/health")
    assert resp_h.status_code == 200
    assert resp_h.json()["status"] == "healthy"

    # Restore
    router_module.write_transition_record = original_write  # type: ignore[attr-defined]

    print("PASS")
