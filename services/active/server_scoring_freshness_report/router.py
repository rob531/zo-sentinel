# deps: fastapi, pydantic, sqlalchemy
"""server_scoring_freshness_report – staleness report for MCP server LLM scores.

GET /api/scoring/freshness  returns stale + never-scored server lists.
Public endpoint (auth=public); data from app Postgres via get_session.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── MUST be before any `from app.` import ────────────────────────────────────
_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_scoring_freshness_report"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class StaleServerItem(BaseModel):
    server_id: str
    name: Optional[str]
    days_since_last_score: float
    risk_tier: Optional[str]
    last_scanned: Optional[datetime]
    last_score_at: Optional[datetime]


class NeverScoredItem(BaseModel):
    server_id: str
    name: Optional[str]
    first_seen: Optional[datetime]
    risk_tier: Optional[str]


class FreshnessSummary(BaseModel):
    total_servers: int
    stale_count: int
    never_scored_count: int
    fresh_count: int


class FreshnessResponse(BaseModel):
    threshold_days: int
    summary: FreshnessSummary
    stale_servers: list[StaleServerItem]
    never_scored: list[NeverScoredItem]


# --------------------------------------------------------------------------- #
# Business logic (usable without FastAPI)
# --------------------------------------------------------------------------- #

def compute_scoring_freshness(
    threshold_days: int,
    db: Session,
) -> FreshnessResponse:
    """
    Classify every server in mcp_server_registry by its last axis-score timestamp:
      - stale       : MAX(scored_at) > threshold_days ago
      - never_scored: no entry in mcp_llm_axis_scores
      - fresh       : everything else
    """
    now = datetime.now(timezone.utc)

    max_score_sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
            McpServerRegistry.first_seen,
            max_score_sub.c.max_scored_at,
        )
        .outerjoin(
            max_score_sub,
            McpServerRegistry.server_id == max_score_sub.c.server_id,
        )
        .all()
    )

    stale_list: list[StaleServerItem] = []
    never_scored_list: list[NeverScoredItem] = []
    fresh_count = 0

    for row in rows:
        max_scored_at: Optional[datetime] = row.max_scored_at

        if max_scored_at is None:
            never_scored_list.append(
                NeverScoredItem(
                    server_id=row.server_id,
                    name=row.name,
                    first_seen=row.first_seen,
                    risk_tier=row.risk_tier,
                )
            )
        else:
            if max_scored_at.tzinfo is None:
                max_scored_at = max_scored_at.replace(tzinfo=timezone.utc)
            days_since = (now - max_scored_at).total_seconds() / 86400.0

            if days_since > threshold_days:
                stale_list.append(
                    StaleServerItem(
                        server_id=row.server_id,
                        name=row.name,
                        days_since_last_score=round(days_since, 2),
                        risk_tier=row.risk_tier,
                        last_scanned=row.last_scanned,
                        last_score_at=max_scored_at,
                    )
                )
            else:
                fresh_count += 1

    total_servers = len(rows)
    return FreshnessResponse(
        threshold_days=threshold_days,
        summary=FreshnessSummary(
            total_servers=total_servers,
            stale_count=len(stale_list),
            never_scored_count=len(never_scored_list),
            fresh_count=fresh_count,
        ),
        stale_servers=stale_list,
        never_scored=never_scored_list,
    )


# --------------------------------------------------------------------------- #
# FastAPI endpoint
# --------------------------------------------------------------------------- #

@router.get("/scoring/freshness", response_model=FreshnessResponse)
def get_freshness(
    threshold_days: int = Query(default=30, ge=0),
    db: Session = Depends(get_session),
) -> FreshnessResponse:
    """Return stale, never-scored, and fresh server counts with per-server detail."""
    return compute_scoring_freshness(threshold_days, db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    McpLlmAxisScore.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_db = TestSession()

    now = datetime.now(timezone.utc)

    test_servers = [
        McpServerRegistry(
            server_id="srv-stale-high",
            name="Stale High",
            risk_tier="high",
            first_seen=now - timedelta(days=100),
            last_scanned=now - timedelta(days=60),
            last_assessed=now - timedelta(days=60),
            last_seen=now - timedelta(days=60),
            registry_source="test",
            scan_count=5,
            trust_score=20,
            confidence=50,
            description="stale server",
            meta="{}",
            url="http://stale-high.example",
            verdict="risky",
            verdict_reasoning="old score",
        ),
        McpServerRegistry(
            server_id="srv-stale-medium",
            name="Stale Medium",
            risk_tier="medium",
            first_seen=now - timedelta(days=90),
            last_scanned=now - timedelta(days=70),
            last_assessed=now - timedelta(days=70),
            last_seen=now - timedelta(days=70),
            registry_source="test",
            scan_count=3,
            trust_score=40,
            confidence=60,
            description="stale medium",
            meta="{}",
            url="http://stale-medium.example",
            verdict="moderate",
            verdict_reasoning="old score",
        ),
        McpServerRegistry(
            server_id="srv-never",
            name="Never Scored",
            risk_tier="low",
            first_seen=now - timedelta(days=50),
            last_scanned=None,
            last_assessed=None,
            last_seen=now - timedelta(days=50),
            registry_source="test",
            scan_count=0,
            trust_score=50,
            confidence=30,
            description="no scores ever",
            meta="{}",
            url="http://never.example",
            verdict="unknown",
            verdict_reasoning="no scores",
        ),
        McpServerRegistry(
            server_id="srv-fresh-high",
            name="Fresh High",
            risk_tier="high",
            first_seen=now - timedelta(days=200),
            last_scanned=now - timedelta(days=5),
            last_assessed=now - timedelta(days=5),
            last_seen=now - timedelta(days=5),
            registry_source="test",
            scan_count=20,
            trust_score=80,
            confidence=90,
            description="fresh high",
            meta="{}",
            url="http://fresh-high.example",
            verdict="risky",
            verdict_reasoning="fresh",
        ),
        McpServerRegistry(
            server_id="srv-fresh-low",
            name="Fresh Low",
            risk_tier="low",
            first_seen=now - timedelta(days=150),
            last_scanned=now - timedelta(days=1),
            last_assessed=now - timedelta(days=1),
            last_seen=now - timedelta(days=1),
            registry_source="test",
            scan_count=25,
            trust_score=90,
            confidence=95,
            description="fresh low",
            meta="{}",
            url="http://fresh-low.example",
            verdict="safe",
            verdict_reasoning="fresh low",
        ),
    ]
    test_db.add_all(test_servers)

    stale_time = now - timedelta(days=40)
    fresh_time = now - timedelta(days=5)

    test_scores = [
        McpLlmAxisScore(
            server_id="srv-stale-high", axis_name="overall_risk",
            label="risky", label_index=2, probs="{}",
            p_top=0.5, p_critical=0.2, p_danger=0.3,
            escalated=False, escalated_to=None,
            decision_rule_version="v1", model_version="prod-2025",
            adapter_sha256="abc123", scored_at=stale_time,
        ),
        McpLlmAxisScore(
            server_id="srv-stale-medium", axis_name="overall_risk",
            label="moderate", label_index=1, probs="{}",
            p_top=0.4, p_critical=0.1, p_danger=0.5,
            escalated=False, escalated_to=None,
            decision_rule_version="v1", model_version="prod-2025",
            adapter_sha256="def456", scored_at=stale_time,
        ),
        McpLlmAxisScore(
            server_id="srv-fresh-high", axis_name="overall_risk",
            label="risky", label_index=2, probs="{}",
            p_top=0.6, p_critical=0.3, p_danger=0.1,
            escalated=False, escalated_to=None,
            decision_rule_version="v1", model_version="prod-2025",
            adapter_sha256="ghi789", scored_at=fresh_time,
        ),
        McpLlmAxisScore(
            server_id="srv-fresh-low", axis_name="overall_risk",
            label="safe", label_index=0, probs="{}",
            p_top=0.9, p_critical=0.0, p_danger=0.1,
            escalated=False, escalated_to=None,
            decision_rule_version="v1", model_version="prod-2025",
            adapter_sha256="jkl012", scored_at=fresh_time,
        ),
    ]
    test_db.add_all(test_scores)
    test_db.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session() -> Session:
        return test_db

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(main_app)

    resp = client.get("/api/scoring/freshness?threshold_days=30")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    assert data["summary"]["total_servers"] == 5, f"total_servers=5, got {data['summary']}"
    assert data["summary"]["stale_count"] == 2, f"Expected stale_count=2, got {data['summary']['stale_count']}"
    assert data["summary"]["never_scored_count"] == 1, f"Expected never_scored_count=1, got {data['summary']['never_scored_count']}"
    assert data["summary"]["fresh_count"] == 2, f"Expected fresh_count=2, got {data['summary']['fresh_count']}"

    stale_ids = [s["server_id"] for s in data["stale_servers"]]
    assert "srv-stale-high" in stale_ids, f"srv-stale-high expected in stale_servers, got {stale_ids}"
    assert "srv-stale-medium" in stale_ids, f"srv-stale-medium expected in stale_servers, got {stale_ids}"

    never_ids = [s["server_id"] for s in data["never_scored"]]
    assert "srv-never" in never_ids, f"srv-never expected in never_scored, got {never_ids}"

    test_db.close()
    print("PASS")
    sys.exit(0)
