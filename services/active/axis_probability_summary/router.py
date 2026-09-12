# deps: fastapi, sqlalchemy, pydantic
"""Axis Probability Summary API.

GET /api/servers/{server_id}/axis-probability-summary
  Returns per-axis probability summary (p_top, p_critical, p_danger, probs)
  for the most-recent score of each axis for a given server.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_probability_summary"])


# --- Pydantic response models -----------------------------------------------

class AxisProbability(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float = Field(..., ge=0.0, le=1.0)
    p_critical: float = Field(..., ge=0.0, le=1.0)
    p_danger: float = Field(..., ge=0.0, le=1.0)
    probs: dict[str, float] = Field(default_factory=dict, description="Probability distribution dict from model")
    escalated: bool
    escalated_to: Optional[str] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None
    scored_at: str  # ISO-8601


class AxisProbabilitySummaryResponse(BaseModel):
    server_id: str
    axes: list[AxisProbability]
    axis_count: int


# --- Helpers ---------------------------------------------------------------

def _parse_probs(raw: Any) -> dict[str, float]:
    """Deserialize the JSON probs column into a dict of float values."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {k: float(v) for k, v in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {k: float(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _score_to_axis_probability(score: McpLlmAxisScore) -> AxisProbability:
    return AxisProbability(
        axis_name=score.axis_name,
        label=score.label or "",
        label_index=score.label_index or 0,
        p_top=score.p_top if score.p_top is not None else 0.0,
        p_critical=score.p_critical if score.p_critical is not None else 0.0,
        p_danger=score.p_danger if score.p_danger is not None else 0.0,
        probs=_parse_probs(score.probs),
        escalated=bool(score.escalated),
        escalated_to=score.escalated_to,
        decision_rule_version=score.decision_rule_version,
        model_version=score.model_version,
        adapter_sha256=score.adapter_sha256,
        scored_at=(
            score.scored_at.isoformat()
            if isinstance(score.scored_at, datetime)
            else str(score.scored_at or "")
        ),
    )


# --- Endpoint --------------------------------------------------------------

@router.get(
    "/servers/{server_id}/axis-probability-summary",
    response_model=AxisProbabilitySummaryResponse,
    name="axis_probability_summary:get",
)
def get_axis_probability_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> AxisProbabilitySummaryResponse:
    """Return the most-recent per-axis probability summary for a server."""
    # Verify server exists
    server = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id!r} not found",
        )

    # Fetch all scores for this server, ordered newest-first
    rows = (
        db.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
        )
        .scalars()
        .all()
    )

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No axis scores found for server {server_id!r}",
        )

    # Deduplicate to most-recent score per axis_name
    seen: set[str] = set()
    axes: list[AxisProbability] = []
    for score in rows:
        if score.axis_name not in seen:
            seen.add(score.axis_name)
            axes.append(_score_to_axis_probability(score))

    axes.sort(key=lambda a: a.axis_name)
    return AxisProbabilitySummaryResponse(
        server_id=server_id,
        axes=axes,
        axis_count=len(axes),
    )


# --- Self-test ------------------------------------------------------------

if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from app.models import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    now = datetime.now(timezone.utc)
    srv_id = "test-aps-srv-001"
    axes = [
        ("overall_risk", "CRITICAL", 0, 0.92, 0.98, 0.10, {"critical": 0.92, "high": 0.06, "medium": 0.02}),
        ("auth_strength", "WEAK", 0, 0.20, 0.35, 0.75, {"weak": 0.20, "medium": 0.40, "strong": 0.40}),
        ("data_sensitivity", "LOW", 0, 0.10, 0.15, 0.85, {"low": 0.10, "medium": 0.50, "high": 0.40}),
        ("availability", "HIGH", 0, 0.85, 0.90, 0.15, {"high": 0.85, "medium": 0.10, "low": 0.05}),
    ]

    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id=srv_id,
            name="Test APS Server",
            registry_source="test",
            url="https://test.example.com",
        ))
        row_id = 1
        for axis_name, label, label_idx, p_top, p_crit, p_dang, probs_dict in axes:
            # Older score with distinct model_version (avoid unique constraint violation)
            sess.add(McpLlmAxisScore(
                id=row_id,
                server_id=srv_id,
                axis_name=axis_name,
                label=label,
                label_index=label_idx,
                p_top=p_top - 0.05,
                p_critical=p_crit - 0.05,
                p_danger=p_dang + 0.05,
                probs={k: round(v - 0.05, 4) for k, v in probs_dict.items()},
                escalated=False,
                decision_rule_version="v1.0.0",
                model_version="gpt-4o-old",
                adapter_sha256="a" * 64,
                scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ))
            row_id += 1
            # Newer score with distinct model_version
            sess.add(McpLlmAxisScore(
                id=row_id,
                server_id=srv_id,
                axis_name=axis_name,
                label=label,
                label_index=label_idx,
                p_top=p_top,
                p_critical=p_crit,
                p_danger=p_dang,
                probs=probs_dict,
                escalated=False,
                decision_rule_version="v1.0.0",
                model_version="gpt-4o-new",
                adapter_sha256="a" * 64,
                scored_at=now,
            ))
            row_id += 1
        sess.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    # Test 1: happy path
    resp = client.get(f"/api/servers/{srv_id}/axis-probability-summary")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != srv_id:
        print(f"FAIL: server_id mismatch: {data['server_id']}")
        sys.exit(1)
    if data["axis_count"] != 4:
        print(f"FAIL: expected 4 axes, got {data['axis_count']}")
        sys.exit(1)
    if len(data["axes"]) != 4:
        print(f"FAIL: expected 4 axis objects, got {len(data['axes'])}")
        sys.exit(1)

    # Test 2: de-duplicated to newest per axis
    for axis in data["axes"]:
        if axis["axis_name"] == "overall_risk":
            if not (0.0 <= axis["p_top"] <= 1.0):
                print(f"FAIL: p_top {axis['p_top']} out of [0,1]")
                sys.exit(1)
            if axis["p_top"] != 0.92:
                print(f"FAIL: expected newest score (p_top=0.92), got {axis['p_top']}")
                sys.exit(1)
            if not isinstance(axis["probs"], dict):
                print(f"FAIL: probs should be dict, got {type(axis['probs'])}")
                sys.exit(1)
            if axis["probs"].get("critical", 0) != 0.92:
                print(f"FAIL: probs dict value mismatch: {axis['probs']}")
                sys.exit(1)

    # Test 3: sorted by axis_name
    axis_names = [a["axis_name"] for a in data["axes"]]
    if axis_names != sorted(axis_names):
        print(f"FAIL: axes not sorted: {axis_names}")
        sys.exit(1)

    # Test 4: 404 for unknown server
    resp2 = client.get("/api/servers/nonexistent-server/axis-probability-summary")
    if resp2.status_code != 404:
        print(f"FAIL: unknown server should 404, got {resp2.status_code}")
        sys.exit(1)

    # Test 5: 404 for server with no scores
    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="test-aps-srv-no-scores",
            name="No Scores Server",
            registry_source="test",
            url="https://no-scores.example.com",
        ))
        sess.commit()

    resp3 = client.get("/api/servers/test-aps-srv-no-scores/axis-probability-summary")
    if resp3.status_code != 404:
        print(f"FAIL: server with no scores should 404, got {resp3.status_code}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
