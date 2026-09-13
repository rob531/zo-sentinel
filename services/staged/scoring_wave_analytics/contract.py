# services/staged/scoring_wave_analytics/contract.py
"""
FastAPI contract for the *scoring_wave_analytics* service.

Provides a single endpoint:
GET /api/scoring/wave-analytics
which returns analytics about scoring throughput and backlog.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

# Real application data layer – must be used exactly as in the app.
from app.db import get_session, Base  # noqa: F401  (Base needed for test DB creation)
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class DailyRateItem(BaseModel):
    date: date
    scored_count: int


class WaveAnalyticsResponse(BaseModel):
    total_servers: int
    recently_scored: int
    never_scored: int
    avg_score_age_days: float
    daily_rate: List[DailyRateItem]


@router.get(
    "/api/scoring/wave-analytics",
    response_model=WaveAnalyticsResponse,
    tags=["scoring_wave_analytics"],
)
def get_wave_analytics(db: Session = Depends(get_session)) -> WaveAnalyticsResponse:
    """Compute scoring analytics."""
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    # ------------------------------------------------------------------ #
    # 1. Total servers
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # ------------------------------------------------------------------ #
    # 2. Servers scored in the last 7 days (distinct server_id)
    recently_scored = (
        db.query(func.count(distinct(McpLlmAxisScore.server_id)))
        .filter(McpLlmAxisScore.scored_at >= seven_days_ago)
        .scalar()
        or 0
    )

    # ------------------------------------------------------------------ #
    # 3. Servers that have never been scored
    # Sub‑query of servers that have at least one score
    scored_server_ids_subq = (
        db.query(McpLlmAxisScore.server_id).distinct().subquery()
    )
    never_scored = (
        db.query(func.count(McpServerRegistry.server_id))
        .filter(~McpServerRegistry.server_id.in_(scored_server_ids_subq))
        .scalar()
        or 0
    )

    # ------------------------------------------------------------------ #
    # 4. Average age of all axis scores (in days)
    score_ages_seconds = [
        (now - row[0]).total_seconds()
        for row in db.query(McpLlmAxisScore.scored_at).all()
        if row[0] is not None
    ]
    if score_ages_seconds:
        avg_score_age_days = sum(score_ages_seconds) / len(score_ages_seconds) / 86400
    else:
        avg_score_age_days = 0.0

    # ------------------------------------------------------------------ #
    # 5. Per‑day scoring rate for the last 14 days (distinct servers per day)
    daily_rate: List[DailyRateItem] = []
    for offset in range(14):
        day = (now - timedelta(days=offset)).date()
        start_dt = datetime.combine(day, datetime.min.time())
        end_dt = datetime.combine(day, datetime.max.time())
        count = (
            db.query(func.count(distinct(McpLlmAxisScore.server_id)))
            .filter(McpLlmAxisScore.scored_at >= start_dt, McpLlmAxisScore.scored_at <= end_dt)
            .scalar()
            or 0
        )
        daily_rate.append(DailyRateItem(date=day, scored_count=count))

    # Return results (most recent day last)
    return WaveAnalyticsResponse(
        total_servers=total_servers,
        recently_scored=recently_scored,
        never_scored=never_scored,
        avg_score_age_days=avg_score_age_days,
        daily_rate=list(reversed(daily_rate)),
    )


# --------------------------------------------------------------------------- #
# Self‑test ---------------------------------------------------------------
# Run with: python -m services.staged.scoring_wave_analytics.contract
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override for the test app
    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Assemble FastAPI app with the router and the override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data:
    #   10 servers (ids srv0‑srv9)
    #   - srv0‑srv4 scored recently (within 7 days)
    #   - srv5‑srv7 scored older (10 days ago)
    #   - srv8‑srv9 never scored
    now = datetime.utcnow()
    db = TestSessionLocal()
    servers = [
        McpServerRegistry(
            server_id=f"srv{i}",
            name=f"Server {i}",
            confidence=0.5,
            description="test server",
            first_seen=now,
            last_seen=now,
            registry_source="test",
            risk_tier="low",
            scan_count=0,
            trust_score=0.5,
            url=f"http://example.com/{i}",
            verdict="clean",
            verdict_reasoning="test",
        )
        for i in range(10)
    ]
    db.add_all(servers)
    db.flush()  # obtain PKs if needed

    # Recent scores (1 day ago)
    recent_scores = [
        McpLlmAxisScore(
            server_id=f"srv{i}",
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=i,
            label="label",
            label_index=0,
            model_version="1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.3,
            probs="{}",
            scored_at=now - timedelta(days=1),
            adapter_sha256="sha256dummy",
        )
        for i in range(5)
    ]

    # Older scores (10 days ago)
    older_scores = [
        McpLlmAxisScore(
            server_id=f"srv{i}",
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=i,
            label="label",
            label_index=0,
            model_version="1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.3,
            probs="{}",
            scored_at=now - timedelta(days=10),
            adapter_sha256="sha256dummy",
        )
        for i in range(5, 8)
    ]

    db.add_all(recent_scores + older_scores)
    db.commit()
    db.close()

    # ------------------------------------------------------------------- #
    # Execute test request
    client = TestClient(app)
    response = client.get("/api/scoring/wave-analytics")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert payload["never_scored"] == 2, f"never_scored={payload['never_scored']}"
    assert len(payload["daily_rate"]) > 0, "daily_rate list is empty"
    print("PASS")