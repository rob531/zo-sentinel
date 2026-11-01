"""Data and computation layer for the dispute tier-shift queue."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


class TierShiftItem(BaseModel):
    server_id: str
    name: Optional[str]
    current_tier: Optional[str]
    proposed_tier: str
    delta: int
    reason_category: Optional[str]
    status: str
    created_at: Optional[str]
    top_axis: Optional[str]
    top_axis_p_top: Optional[float]


class TierShiftQueue(BaseModel):
    counts: Dict[str, int]
    queue: List[TierShiftItem]


router = APIRouter(prefix="/api", tags=["disputes"])

_TIER_RANKS = {
    "trusted": 0,
    "caution": 1,
    "elevated": 2,
    "high": 3,
    "critical": 4,
}
_UNKNOWN_RANK = -1


def _tier_rank(tier: Optional[str]) -> int:
    if tier is None:
        return _UNKNOWN_RANK
    return _TIER_RANKS.get(str(tier).strip().lower(), _UNKNOWN_RANK)


def _timestamp(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _score_timestamp(value: object) -> str:
    return _timestamp(value)


def _float_value(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_tier_shift_queue(
    db: Session,
    status: Optional[str] = None,
    limit: int = 50,
) -> TierShiftQueue:
    """Build the queue and status counts from the application database."""
    limit = max(1, min(int(limit), 200))

    count_rows = db.execute(
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    ).all()
    counts = {"open": 0, "under_review": 0, "resolved": 0, "rejected": 0}
    for row_status, count in count_rows:
        if row_status in counts:
            counts[row_status] = int(count)

    statement = (
        select(McpScoreDispute, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.created_at.desc())
    )
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    dispute_rows = db.execute(statement).all()
    server_ids = [str(dispute.server_id) for dispute, _registry in dispute_rows]

    latest_rows = []
    if server_ids:
        latest_rows = db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
            )
            .where(McpLlmAxisScore.server_id.in_(server_ids))
            .group_by(McpLlmAxisScore.server_id)
        ).all()

    latest_at = {
        str(server_id): _score_timestamp(max_scored_at)
        for server_id, max_scored_at in latest_rows
    }

    score_rows = []
    if server_ids:
        score_rows = db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id.in_(server_ids)
            )
        ).scalars().all()

    scores_by_server: Dict[str, List[McpLlmAxisScore]] = {}
    for score in score_rows:
        scores_by_server.setdefault(str(score.server_id), []).append(score)

    top_scores: Dict[str, McpLlmAxisScore] = {}
    for server_id, scores in scores_by_server.items():
        server_latest = latest_at.get(server_id)
        if server_latest:
            candidates = [
                score
                for score in scores
                if _score_timestamp(score.scored_at) == server_latest
            ]
        else:
            candidates = scores
        if not candidates:
            continue

        def p_top_key(score: McpLlmAxisScore):
            value = _float_value(score.p_top)
            return (value is not None, value if value is not None else float("-inf"))

        top_scores[server_id] = max(candidates, key=p_top_key)

    items: List[TierShiftItem] = []
    for dispute, registry in dispute_rows:
        current_tier = registry.risk_tier
        proposed_tier = dispute.proposed_overall_risk
        current_rank = _tier_rank(current_tier)
        proposed_rank = _tier_rank(proposed_tier)
        top_score = top_scores.get(str(dispute.server_id))
        p_top = _float_value(top_score.p_top) if top_score is not None else None

        items.append(
            TierShiftItem(
                server_id=str(dispute.server_id),
                name=registry.name,
                current_tier=current_tier,
                proposed_tier=str(proposed_tier),
                delta=proposed_rank - current_rank,
                reason_category=dispute.reason_category,
                status=str(dispute.status),
                created_at=(
                    _timestamp(dispute.created_at)
                    if dispute.created_at is not None
                    else None
                ),
                top_axis=top_score.axis_name if top_score is not None else None,
                top_axis_p_top=p_top,
            )
        )

    items.sort(
        key=lambda item: (
            abs(item.delta),
            item.created_at or "",
        ),
        reverse=True,
    )
    items = items[:limit]

    return TierShiftQueue(counts=counts, queue=items)


@router.get("/disputes/tier-shift-queue", response_model=TierShiftQueue)
def get_tier_shift_queue(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> TierShiftQueue:
    return build_tier_shift_queue(db, status=status, limit=limit)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSession()
    db.add_all(
        [
            McpServerRegistry(
                server_id="srv-a",
                name="Server A",
                risk_tier="trusted",
                verdict="safe",
            ),
            McpServerRegistry(
                server_id="srv-b",
                name="Server B",
                risk_tier="caution",
                verdict="caution",
            ),
            McpScoreDispute(
                id=1,
                server_id="srv-a",
                proposed_overall_risk="critical",
                status="open",
                reason_category="risk",
                explanation="Analyst proposed critical",
                submitted_by="analyst",
            ),
            McpScoreDispute(
                id=2,
                server_id="srv-b",
                proposed_overall_risk="elevated",
                status="resolved",
                reason_category="risk",
                explanation="Analyst proposed elevated",
                submitted_by="analyst",
            ),
            McpLlmAxisScore(
                id=1,
                server_id="srv-a",
                axis_name="identity",
                model_version="test-model",
                p_top=0.2,
                scored_at=datetime(2024, 1, 1, 10, 0, 0),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-a",
                axis_name="network",
                model_version="test-model",
                p_top=0.9,
                scored_at=datetime(2024, 1, 1, 11, 0, 0),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv-b",
                axis_name="network",
                model_version="test-model",
                p_top=0.4,
                scored_at=datetime(2024, 1, 1, 10, 0, 0),
            ),
        ]
    )
    db.commit()
    db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestingSession()

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/tier-shift-queue")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "queue" in body
        assert len(body["queue"]) == 2
        assert body["counts"]["open"] == 1
        assert body["queue"][0]["server_id"] == "srv-a"
        assert body["queue"][0]["delta"] > 0
        assert body["queue"][0]["top_axis"] == "network"

    print("PASS")


if __name__ == "__main__":
    _self_test()