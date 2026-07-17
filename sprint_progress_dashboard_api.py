# deps: fastapi, pydantic, sqlalchemy, requests
"""Sprint progress dashboard: scoring velocity, backlog reduction, tier distribution delta."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, and_, literal
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/reporting", tags=["sprint"])


# ---------- Pydantic models ----------

class ScoringVelocity(BaseModel):
    total_scored: int
    avg_per_day: float
    trend: str  # increasing | decreasing | stable


class BacklogMetrics(BaseModel):
    never_scored_start: int
    never_scored_end: int
    reduction: int
    reduction_pct: float


class TierDistributionDelta(BaseModel):
    tier: str
    start_count: int
    end_count: int
    delta: int


class SprintProgressResponse(BaseModel):
    sprint_start: str
    sprint_end: str
    scoring_velocity: ScoringVelocity
    backlog_metrics: BacklogMetrics
    tier_distribution_delta: List[TierDistributionDelta]
    sprint_goal_met: bool


# ---------- Helpers ----------

def _sprint_dates(sprint_days: int) -> tuple[datetime, datetime]:
    sprint_end = datetime.utcnow()
    sprint_start = sprint_end - timedelta(days=sprint_days)
    return sprint_start, sprint_end


def _scoring_velocity(db: Session, sprint_start: datetime, sprint_end: datetime) -> ScoringVelocity:
    """Count overall_risk axis rows scored in sprint; derive trend from first vs second half."""
    total = db.execute(
        select(func.count(McpLlmAxisScore.id)).where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= sprint_start,
            McpLlmAxisScore.scored_at <= sprint_end,
        )
    ).scalar() or 0

    days = (sprint_end - sprint_start).days
    avg_per_day = total / days if days > 0 else 0.0

    half_days = days // 2
    first_half_end = sprint_start + timedelta(days=half_days)

    first_half = db.execute(
        select(func.count(McpLlmAxisScore.id)).where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= sprint_start,
            McpLlmAxisScore.scored_at <= first_half_end,
        )
    ).scalar() or 0

    second_half = total - first_half

    if second_half > first_half:
        trend = "increasing"
    elif second_half < first_half:
        trend = "decreasing"
    else:
        trend = "stable"

    return ScoringVelocity(total_scored=total, avg_per_day=avg_per_day, trend=trend)


def _backlog_metrics(db: Session, sprint_start: datetime, sprint_end: datetime) -> BacklogMetrics:
    """'Never scored' = servers in registry with no overall_risk axis rows at cutoff time.
    Derived via anti-join: registry LEFT JOIN scores, NULL score side = never scored."""

    # Anti-join: servers in registry that have never been scored at start of sprint
    never_start = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.server_id.notin_(
                select(McpLlmAxisScore.server_id).where(
                    McpLlmAxisScore.axis_name == "overall_risk",
                    McpLlmAxisScore.scored_at <= sprint_start,
                )
            )
        )
    ).scalar() or 0

    # Anti-join: servers never scored by end of sprint
    never_end = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.server_id.notin_(
                select(McpLlmAxisScore.server_id).where(
                    McpLlmAxisScore.axis_name == "overall_risk",
                    McpLlmAxisScore.scored_at <= sprint_end,
                )
            )
        )
    ).scalar() or 0

    reduction = never_start - never_end
    reduction_pct = (reduction / never_start * 100) if never_start > 0 else 0.0

    return BacklogMetrics(
        never_scored_start=never_start,
        never_scored_end=never_end,
        reduction=reduction,
        reduction_pct=round(reduction_pct, 2),
    )


def _tier_distribution_delta(
    db: Session, sprint_start: datetime, sprint_end: datetime
) -> List[TierDistributionDelta]:
    """Count registry servers by risk_tier at sprint start vs end."""
    all_tiers = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN", "NONE"]

    results: List[TierDistributionDelta] = []
    for tier in all_tiers:
        start_count = db.execute(
            select(func.count(McpServerRegistry.server_id)).where(
                McpServerRegistry.risk_tier == tier,
                McpServerRegistry.last_assessed <= sprint_start,
            )
        ).scalar() or 0

        end_count = db.execute(
            select(func.count(McpServerRegistry.server_id)).where(
                McpServerRegistry.risk_tier == tier,
                McpServerRegistry.last_assessed <= sprint_end,
            )
        ).scalar() or 0

        results.append(TierDistributionDelta(
            tier=tier,
            start_count=start_count,
            end_count=end_count,
            delta=end_count - start_count,
        ))

    return results


def _sprint_goal_met(backlog: BacklogMetrics) -> bool:
    """Sprint goal: reduce never-scored backlog by >= 10%."""
    return backlog.reduction_pct >= 10.0


# ---------- Endpoint ----------

@router.get("/sprint-progress", response_model=SprintProgressResponse)
def sprint_progress(
    sprint_days: int = Query(14, description="Number of days in the sprint"),
    db: Session = Depends(get_session),
) -> SprintProgressResponse:
    sprint_start, sprint_end = _sprint_dates(sprint_days)
    velocity = _scoring_velocity(db, sprint_start, sprint_end)
    backlog = _backlog_metrics(db, sprint_start, sprint_end)
    tier_delta = _tier_distribution_delta(db, sprint_start, sprint_end)
    goal_met = _sprint_goal_met(backlog)

    return SprintProgressResponse(
        sprint_start=sprint_start.isoformat(),
        sprint_end=sprint_end.isoformat(),
        scoring_velocity=velocity,
        backlog_metrics=backlog,
        tier_distribution_delta=tier_delta,
        sprint_goal_met=goal_met,
    )


# ---------- Self-test ----------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: 3 scores in first half, 1 in second half; 5 registry rows
    # server1 scored twice (at day 10, 5); server2 once; server3 never scored
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Alpha", url="https://a.com",
                            registry_source="npm", risk_tier="HIGH",
                            last_assessed=datetime.utcnow() - timedelta(days=5)))
    s.add(McpServerRegistry(server_id="srv2", name="Beta", url="https://b.com",
                            registry_source="npm", risk_tier="MEDIUM",
                            last_assessed=datetime.utcnow() - timedelta(days=3)))
    s.add(McpServerRegistry(server_id="srv3", name="Gamma", url="https://c.com",
                            registry_source="github", risk_tier=None,
                            last_assessed=None))
    s.add(McpServerRegistry(server_id="srv4", name="Delta", url="https://d.com",
                            registry_source="npm", risk_tier="LOW",
                            last_assessed=datetime.utcnow() - timedelta(days=12)))
    s.add(McpServerRegistry(server_id="srv5", name="Epsilon", url="https://e.com",
                            registry_source="github", risk_tier="HIGH",
                            last_assessed=datetime.utcnow() - timedelta(days=12)))
    now = datetime.utcnow()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559",
                          scored_at=now - timedelta(days=10)))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="capability_breadth",
                          label="BROAD", model_version="v3.0_40974559",
                          scored_at=now - timedelta(days=5)))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="overall_risk",
                          label="MEDIUM", model_version="v3.0_40974559",
                          scored_at=now - timedelta(days=8)))
    s.add(McpLlmAxisScore(id=4, server_id="srv3", axis_name="other_axis",
                          label="LOW", model_version="v3.0_40974559",
                          scored_at=now - timedelta(days=1)))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    r = c.get("/reporting/sprint-progress?sprint_days=14")
    assert r.status_code == 200, r.text
    j = r.json()

    # sprint_goal_met is bool
    assert isinstance(j["sprint_goal_met"], bool), f"sprint_goal_met={j['sprint_goal_met']!r}"
    # avg_per_day >= 0
    assert j["scoring_velocity"]["avg_per_day"] >= 0, j["scoring_velocity"]
    # has required fields
    assert "sprint_start" in j and "sprint_end" in j
    assert "scoring_velocity" in j
    assert "backlog_metrics" in j
    assert "tier_distribution_delta" in j
    print("PASS")
