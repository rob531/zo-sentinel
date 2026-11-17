from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


STATUS_KEYS = ("open", "in_review", "resolved", "rejected", "escalated")
TIER_KEYS = ("low", "medium", "high")


class AgingStats(BaseModel):
    oldest_open_days: float
    median_open_age_days: float


class DisputeStatsResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: Dict[str, int]
    by_proposed_tier: Dict[str, int]
    aging: AgingStats
    generated_at: str


app = FastAPI(title="Score Dispute Resolution Stats API")


@app.get("/api/disputes/stats", response_model=DisputeStatsResponse)
def get_dispute_stats(
    days: int = Query(default=30, ge=1),
    session=Depends(get_session),
) -> DisputeStatsResponse:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    disputes = session.query(McpScoreDispute).filter(
        McpScoreDispute.created_at >= cutoff
    ).all()

    by_status = {key: 0 for key in STATUS_KEYS}
    by_reason_category = {}
    by_proposed_tier = {key: 0 for key in TIER_KEYS}
    open_ages = []

    for dispute in disputes:
        status = dispute.status or "open"
        by_status[status] = by_status.get(status, 0) + 1

        reason = dispute.reason_category or "unspecified"
        by_reason_category[reason] = by_reason_category.get(reason, 0) + 1

        tier = dispute.proposed_overall_risk or "medium"
        by_proposed_tier[tier] = by_proposed_tier.get(tier, 0) + 1

        if status == "open" and dispute.created_at is not None:
            created_at = dispute.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
            open_ages.append(age_days)

    oldest_open_days = max(open_ages) if open_ages else 0.0
    median_open_age_days = float(median(open_ages)) if open_ages else 0.0

    return DisputeStatsResponse(
        total=len(disputes),
        by_status=by_status,
        by_reason_category=by_reason_category,
        by_proposed_tier=by_proposed_tier,
        aging=AgingStats(
            oldest_open_days=oldest_open_days,
            median_open_age_days=median_open_age_days,
        ),
        generated_at=now.isoformat(),
    )


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpScoreDispute.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    older = now - timedelta(days=10)
    newer = now - timedelta(days=2)

    seed_rows = [
        McpScoreDispute(
            id=1,
            server_id="server-1",
            status="open",
            reason_category="scoring_accuracy",
            proposed_overall_risk="high",
            explanation="Disputed score requires analyst review.",
            submitted_by="analyst-1",
            created_at=older,
        ),
        McpScoreDispute(
            id=2,
            server_id="server-2",
            status="open",
            reason_category="model_variance",
            proposed_overall_risk="medium",
            explanation="Disputed score requires analyst review.",
            submitted_by="analyst-1",
            created_at=newer,
        ),
        McpScoreDispute(
            id=3,
            server_id="server-3",
            status="in_review",
            reason_category="scoring_accuracy",
            proposed_overall_risk="high",
            explanation="Disputed score is under review.",
            submitted_by="analyst-2",
            created_at=older,
        ),
        McpScoreDispute(
            id=4,
            server_id="server-4",
            status="in_review",
            reason_category="data_quality",
            proposed_overall_risk="low",
            explanation="Disputed score is under review.",
            submitted_by="analyst-2",
            created_at=newer,
        ),
        McpScoreDispute(
            id=5,
            server_id="server-5",
            status="resolved",
            reason_category="model_variance",
            proposed_overall_risk="medium",
            explanation="Disputed score was resolved.",
            submitted_by="analyst-1",
            created_at=older,
            resolved_at=older + timedelta(days=1),
        ),
        McpScoreDispute(
            id=6,
            server_id="server-6",
            status="resolved",
            reason_category="data_quality",
            proposed_overall_risk="low",
            explanation="Disputed score was resolved.",
            submitted_by="analyst-2",
            created_at=newer,
            resolved_at=newer + timedelta(days=1),
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    testing_app = FastAPI()
    testing_app.dependency_overrides[get_session] = override_get_session
    testing_app.add_api_route(
        "/api/disputes/stats",
        get_dispute_stats,
        methods=["GET"],
        response_model=DisputeStatsResponse,
    )

    with TestClient(testing_app) as client:
        response = client.get("/api/disputes/stats", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == len(seed_rows)
        assert set(STATUS_KEYS).issubset(payload["by_status"])
        assert set(TIER_KEYS).issubset(payload["by_proposed_tier"])
        assert payload["aging"]["oldest_open_days"] >= 0
        assert payload["aging"]["median_open_age_days"] >= 0

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()