# deps: fastapi, sqlalchemy, pydantic
"""Scoring Run Summary API -- aggregate scoring activity across the registry.

GET /api/scoring/run-summary
  Returns high-level scoring run stats: today's row count, unique servers scored
  in 24h/7d windows, score coverage vs registry, newest/oldest timestamps.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores and
  mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_run_summary"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class ScoringRunSummary(BaseModel):
    """High-level scoring run summary across all servers."""

    total_scores_today: int = Field(..., description="Axis-score rows scored today (UTC)")
    total_servers_scored: int = Field(..., description="Distinct servers that have ever been scored")
    servers_scored_last_24h: int = Field(..., description="Distinct servers scored in the last 24 h")
    servers_scored_last_7d: int = Field(..., description="Distinct servers scored in the last 7 d")
    avg_scores_per_server: float = Field(..., description="Average scores per server (today total / scored servers)")
    score_coverage_pct: float = Field(..., description="Pct of registry servers that have at least one score")
    newest_score_at: Optional[datetime] = Field(None, description="Most-recent score timestamp in the DB")
    oldest_score_at: Optional[datetime] = Field(None, description="Earliest score timestamp in the DB")

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/scoring/run-summary", response_model=ScoringRunSummary)
def get_scoring_run_summary(
    db: Session = Depends(get_session),
) -> ScoringRunSummary:
    """
    Aggregate scoring activity across the MCP server registry.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    # total scores today
    total_scores_today = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at >= today_start)
        .count()
    )

    # distinct servers ever scored
    total_servers_scored = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .count()
    )

    # distinct servers in last 24 h
    servers_scored_last_24h = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= last_24h)
        .distinct()
        .count()
    )

    # distinct servers in last 7 d
    servers_scored_last_7d = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= last_7d)
        .distinct()
        .count()
    )

    # registry total
    total_registry = db.query(McpServerRegistry.server_id).count()

    # averages and coverage
    avg_scores_per_server = (
        round(total_scores_today / total_servers_scored, 2)
        if total_servers_scored > 0
        else 0.0
    )
    score_coverage_pct = (
        round(total_servers_scored / total_registry * 100, 2)
        if total_registry > 0
        else 0.0
    )

    newest_row = (
        db.query(McpLlmAxisScore.scored_at)
        .filter(McpLlmAxisScore.scored_at.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    newest_score_at = newest_row[0] if newest_row else None

    oldest_row = (
        db.query(McpLlmAxisScore.scored_at)
        .filter(McpLlmAxisScore.scored_at.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.asc())
        .first()
    )
    oldest_score_at = oldest_row[0] if oldest_row else None

    return ScoringRunSummary(
        total_scores_today=total_scores_today,
        total_servers_scored=total_servers_scored,
        servers_scored_last_24h=servers_scored_last_24h,
        servers_scored_last_7d=servers_scored_last_7d,
        avg_scores_per_server=avg_scores_per_server,
        score_coverage_pct=score_coverage_pct,
        newest_score_at=newest_score_at,
        oldest_score_at=oldest_score_at,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    now = datetime.now(timezone.utc)

    with _SessionLocal() as db:
        # 3 servers in registry
        db.add_all([
            McpServerRegistry(
                server_id="srv-1", name="Server One", registry_source="test",
                risk_tier="high", confidence=0.9, description="",
                trust_score=0.5, url="http://s1.example", verdict="",
                verdict_reasoning="", first_seen=now, last_assessed=now,
                last_scanned=now, last_seen=now, meta="{}", scan_count=1,
            ),
            McpServerRegistry(
                server_id="srv-2", name="Server Two", registry_source="test",
                risk_tier="medium", confidence=0.8, description="",
                trust_score=0.6, url="http://s2.example", verdict="",
                verdict_reasoning="", first_seen=now, last_assessed=now,
                last_scanned=now, last_seen=now, meta="{}", scan_count=1,
            ),
            McpServerRegistry(
                server_id="srv-3", name="Server Three", registry_source="test",
                risk_tier="low", confidence=0.7, description="",
                trust_score=0.8, url="http://s3.example", verdict="",
                verdict_reasoning="", first_seen=now, last_assessed=now,
                last_scanned=now, last_seen=now, meta="{}", scan_count=1,
            ),
        ])
        db.flush()

        today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        # today: srv-1 (3 scores), srv-2 (2 scores) -- 5 total
        db.add_all([
            McpLlmAxisScore(
                server_id="srv-1", axis_name="overall_risk", model_version="m1",
                label="HIGH", label_index=2, p_top=0.9, p_critical=0.1, p_danger=0.2,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="a" * 64,
                scored_at=today,
            ),
            McpLlmAxisScore(
                server_id="srv-1", axis_name="auth_strength", model_version="m1",
                label="MEDIUM", label_index=1, p_top=0.7, p_critical=0.05, p_danger=0.1,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="a" * 64,
                scored_at=today + timedelta(minutes=30),
            ),
            McpLlmAxisScore(
                server_id="srv-1", axis_name="data_sensitivity", model_version="m1",
                label="LOW", label_index=0, p_top=0.3, p_critical=0.01, p_danger=0.05,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="a" * 64,
                scored_at=today + timedelta(hours=1),
            ),
            McpLlmAxisScore(
                server_id="srv-2", axis_name="overall_risk", model_version="m1",
                label="MEDIUM", label_index=1, p_top=0.5, p_critical=0.05, p_danger=0.15,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="b" * 64,
                scored_at=today + timedelta(hours=2),
            ),
            McpLlmAxisScore(
                server_id="srv-2", axis_name="auth_strength", model_version="m1",
                label="HIGH", label_index=2, p_top=0.8, p_critical=0.1, p_danger=0.2,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="b" * 64,
                scored_at=today + timedelta(hours=3),
            ),
        ])
        db.flush()

        # 5 days ago: srv-2 and srv-3 (outside 7d window)
        db.add_all([
            McpLlmAxisScore(
                server_id="srv-2", axis_name="overall_risk", model_version="m1",
                label="MEDIUM", label_index=1, p_top=0.55, p_critical=0.06, p_danger=0.14,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="b" * 64,
                scored_at=today - timedelta(days=5),
            ),
            McpLlmAxisScore(
                server_id="srv-3", axis_name="overall_risk", model_version="m1",
                label="LOW", label_index=0, p_top=0.2, p_critical=0.01, p_danger=0.03,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="c" * 64,
                scored_at=today - timedelta(days=5, hours=1),
            ),
        ])
        db.commit()

    def _override():
        sess = _SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    # Override on the real FastAPI app instance (not the package)
    from app.main import app as main_app

    main_app.dependency_overrides[get_session] = _override

    _client = TestClient(main_app)

    # Happy path
    resp = _client.get("/api/scoring/run-summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    for field in (
        "total_scores_today", "total_servers_scored", "servers_scored_last_24h",
        "servers_scored_last_7d", "avg_scores_per_server", "score_coverage_pct",
        "newest_score_at", "oldest_score_at",
    ):
        assert field in data, f"Missing field: {field}"

    # Value assertions
    assert data["total_scores_today"] == 5, f"Expected 5, got {data['total_scores_today']}"
    assert data["total_servers_scored"] == 3, f"Expected 3, got {data['total_servers_scored']}"
    # All 3 servers appear in the DB at some point
    assert data["score_coverage_pct"] == 100.0, f"Expected 100.0, got {data['score_coverage_pct']}"
    # Public endpoint -- no 401/403
    print("PASS")
    sys.exit(0)
