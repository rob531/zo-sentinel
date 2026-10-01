# deps: fastapi, pydantic, sqlalchemy
"""Scoring Analytics API.

Aggregates scoring activity metrics across the MCP server registry.
GET /api/scoring/analytics
    Returns total/scorable/unscored servers, average overall_risk p_top,
    coverage %, stale server count, and recent scoring wave count for a
    configurable lookback window (default 30 days).

Auth        : public  (PRODUCT_SPEC §9 scope).
Data plane  : app tier via get_session + SQLAlchemy ORM on
              mcp_server_registry and mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring", tags=["scoring_analytics_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class ScoringAnalyticsResponse(BaseModel):
    total_servers: int = Field(..., description="Total servers in the registry")
    scored_servers: int = Field(..., description="Distinct servers with at least one score in the window")
    unscored_servers: int = Field(..., description="Servers with no score in the window")
    avg_overall_score: Optional[float] = Field(
        None, description="Average p_top for overall_risk axis in the window"
    )
    coverage_pct: float = Field(..., ge=0.0, le=100.0, description="Pct of registry with >=1 score")
    stale_servers: int = Field(..., description="Distinct servers last scored before the window")
    recent_waves: int = Field(..., description="Distinct scored_at timestamps in the window")
    days: int = Field(..., description="Lookback window in days")
    computed_at: datetime = Field(..., description="UTC timestamp of computation")

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/analytics", response_model=ScoringAnalyticsResponse)
def get_scoring_analytics(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> ScoringAnalyticsResponse:
    """
    Compute scoring analytics over the configurable lookback window.

    Reads mcp_server_registry and mcp_llm_axis_scores from the app Postgres
    via the injected SQLAlchemy session.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # Total servers in registry
    total_servers = db.query(McpServerRegistry).count()

    # Distinct servers with at least one score in the window
    scored_ids_rows = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .distinct()
        .all()
    )
    scored_ids = [r[0] for r in scored_ids_rows]
    scored_servers = len(scored_ids)
    unscored_servers = max(0, total_servers - scored_servers)

    # Average p_top for overall_risk axis among servers scored in the window
    if scored_ids:
        avg_rows = (
            db.query(McpLlmAxisScore.p_top)
            .filter(
                McpLlmAxisScore.axis_name == "overall_risk",
                McpLlmAxisScore.scored_at >= cutoff,
                McpLlmAxisScore.server_id.in_(scored_ids),
            )
            .all()
        )
        p_top_values = [r[0] for r in avg_rows if r[0] is not None]
        avg_overall_score = round(sum(p_top_values) / len(p_top_values), 4) if p_top_values else None
    else:
        avg_overall_score = None

    coverage_pct = round((scored_servers / total_servers * 100), 2) if total_servers > 0 else 0.0

    # Distinct servers last scored before the window (stale)
    stale_servers = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at < cutoff, McpLlmAxisScore.scored_at.isnot(None))
        .distinct()
        .count()
    )

    # Distinct scored_at timestamps (scoring waves) in the window
    recent_waves = (
        db.query(McpLlmAxisScore.scored_at)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .distinct()
        .count()
    )

    return ScoringAnalyticsResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        avg_overall_score=avg_overall_score,
        coverage_pct=coverage_pct,
        stale_servers=stale_servers,
        recent_waves=recent_waves,
        days=days,
        computed_at=now,
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
    today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

    # Seed test data
    with _SessionLocal() as db:
        # 5 servers in registry
        for i in range(1, 6):
            db.add(McpServerRegistry(
                server_id=f"srv-{i}",
                name=f"Server {i}",
                registry_source="test",
                risk_tier="medium",
                confidence=0.9,
                description="test",
                trust_score=0.5,
                url=f"http://srv{i}.example",
                verdict="",
                verdict_reasoning="",
                first_seen=now,
                last_assessed=now,
                last_scanned=now,
                last_seen=now,
                meta="{}",
                scan_count=1,
            ))
        db.flush()

        # Scores in the last 7 days: srv-1 (2 scores), srv-2 (1), srv-3 (1)
        # window = 7 days covers all of these
        db.add_all([
            McpLlmAxisScore(
                server_id="srv-1", axis_name="overall_risk",
                model_version="m1", label="HIGH", label_index=2,
                p_top=0.88, p_critical=0.1, p_danger=0.2,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="a" * 64,
                scored_at=today_noon,
            ),
            McpLlmAxisScore(
                server_id="srv-1", axis_name="auth_strength",
                model_version="m1", label="MEDIUM", label_index=1,
                p_top=0.72, p_critical=0.05, p_danger=0.1,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="a" * 64,
                scored_at=today_noon + timedelta(hours=1),
            ),
            McpLlmAxisScore(
                server_id="srv-2", axis_name="overall_risk",
                model_version="m1", label="MEDIUM", label_index=1,
                p_top=0.55, p_critical=0.05, p_danger=0.15,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="b" * 64,
                scored_at=today_noon + timedelta(hours=2),
            ),
            McpLlmAxisScore(
                server_id="srv-3", axis_name="overall_risk",
                model_version="m1", label="LOW", label_index=0,
                p_top=0.22, p_critical=0.01, p_danger=0.04,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="c" * 64,
                scored_at=today_noon + timedelta(hours=3),
            ),
        ])

        # Old scores (outside 7d window): srv-4 and srv-5
        db.add_all([
            McpLlmAxisScore(
                server_id="srv-4", axis_name="overall_risk",
                model_version="m1", label="HIGH", label_index=2,
                p_top=0.91, p_critical=0.12, p_danger=0.25,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="d" * 64,
                scored_at=today_noon - timedelta(days=10),
            ),
            McpLlmAxisScore(
                server_id="srv-5", axis_name="overall_risk",
                model_version="m1", label="CRITICAL", label_index=3,
                p_top=0.97, p_critical=0.25, p_danger=0.40,
                probs={}, escalated=False, escalated_to=None,
                decision_rule_version="v1", adapter_sha256="e" * 64,
                scored_at=today_noon - timedelta(days=15),
            ),
        ])
        db.commit()

    def _override():
        sess = _SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = _override

    _client = TestClient(main_app)

    # Happy path — 7-day window
    resp = _client.get("/api/scoring/analytics?days=7")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    for field in (
        "total_servers", "scored_servers", "unscored_servers",
        "avg_overall_score", "coverage_pct", "stale_servers",
        "recent_waves", "days", "computed_at",
    ):
        assert field in data, f"Missing field: {field}"

    assert data["total_servers"] == 5, f"Expected 5, got {data['total_servers']}"
    assert data["scored_servers"] == 3, f"Expected 3, got {data['scored_servers']}"
    assert data["unscored_servers"] == 2, f"Expected 2, got {data['unscored_servers']}"
    assert isinstance(data["coverage_pct"], (int, float)), f"coverage_pct type: {type(data['coverage_pct'])}"
    assert 0.0 <= data["coverage_pct"] <= 100.0, f"coverage_pct out of range: {data['coverage_pct']}"
    assert data["stale_servers"] == 2, f"Expected 2 stale, got {data['stale_servers']}"
    assert data["avg_overall_score"] is not None, "avg_overall_score must not be None"
    assert isinstance(data["avg_overall_score"], float), "avg_overall_score must be float"

    # Auth check: public endpoint returns 200, not 401/403
    assert resp.status_code == 200

    print("PASS")
    sys.exit(0)
