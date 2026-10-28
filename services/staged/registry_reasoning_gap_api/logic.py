from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ReasoningGapTotals(BaseModel):
    missing_reasoning: int
    missing_confidence: int
    stale_verdict: int
    scored: int


class VerdictReasoningGap(BaseModel):
    verdict: str
    missing_reasoning: int
    missing_confidence: int


class ReasoningGapExample(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    reason: str


class ReasoningGapResponse(BaseModel):
    max_age_days: int
    totals: ReasoningGapTotals
    by_verdict: List[VerdictReasoningGap]
    examples: List[ReasoningGapExample]


router = APIRouter()


def _missing_reasoning():
    return or_(
        McpServerRegistry.verdict_reasoning.is_(None),
        func.trim(McpServerRegistry.verdict_reasoning) == "",
    )


def _missing_confidence():
    return McpServerRegistry.confidence.is_(None)


def _stale_verdict(cutoff: datetime):
    return or_(
        McpServerRegistry.last_assessed.is_(None),
        McpServerRegistry.last_assessed < cutoff,
    )


def analyze_reasoning_gap(
    session: Session,
    max_age_days: int = 30,
    limit: int = 50,
) -> ReasoningGapResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    missing_reasoning = _missing_reasoning()
    missing_confidence = _missing_confidence()
    stale_verdict = _stale_verdict(cutoff)

    scored = session.scalar(
        select(func.count()).select_from(McpServerRegistry)
    ) or 0
    missing_reasoning_count = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(missing_reasoning)
    ) or 0
    missing_confidence_count = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(missing_confidence)
    ) or 0
    stale_verdict_count = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(stale_verdict)
    ) or 0

    verdict_rows = session.execute(
        select(
            McpServerRegistry.verdict,
            func.coalesce(
                func.sum(case((missing_reasoning, 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((missing_confidence, 1), else_=0)),
                0,
            ),
        )
        .group_by(McpServerRegistry.verdict)
        .order_by(McpServerRegistry.verdict.asc().nulls_last())
    ).all()

    example_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .where(missing_reasoning)
        .order_by(McpServerRegistry.last_assessed.asc().nulls_last())
        .limit(limit)
    ).all()

    return ReasoningGapResponse(
        max_age_days=max_age_days,
        totals=ReasoningGapTotals(
            missing_reasoning=int(missing_reasoning_count),
            missing_confidence=int(missing_confidence_count),
            stale_verdict=int(stale_verdict_count),
            scored=int(scored),
        ),
        by_verdict=[
            VerdictReasoningGap(
                verdict=verdict or "",
                missing_reasoning=int(reasoning_count),
                missing_confidence=int(confidence_count),
            )
            for verdict, reasoning_count, confidence_count in verdict_rows
        ],
        examples=[
            ReasoningGapExample(
                server_id=server_id or "",
                name=name or "",
                verdict=verdict or "",
                risk_tier=risk_tier or "",
                reason="missing_reasoning",
            )
            for server_id, name, verdict, risk_tier in example_rows
        ],
    )


@router.get(
    "/registry/reasoning-gap",
    response_model=ReasoningGapResponse,
    response_model_exclude_none=False,
)
def get_reasoning_gap(
    max_age_days: int = Query(default=30, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ReasoningGapResponse:
    return analyze_reasoning_gap(
        session=session,
        max_age_days=max_age_days,
        limit=limit,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    with Session(test_engine) as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="missing-reasoning",
                    name="Missing reasoning",
                    verdict="suspicious",
                    verdict_reasoning="   ",
                    confidence=0.9,
                    risk_tier="high",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="missing-confidence",
                    name="Missing confidence",
                    verdict="safe",
                    verdict_reasoning="Complete reasoning",
                    confidence=None,
                    risk_tier="low",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="stale-verdict",
                    name="Stale verdict",
                    verdict="unknown",
                    verdict_reasoning="Complete reasoning",
                    confidence=0.7,
                    risk_tier="medium",
                    last_assessed=now - timedelta(days=40),
                ),
                McpServerRegistry(
                    server_id="complete",
                    name="Complete",
                    verdict="safe",
                    verdict_reasoning="Complete reasoning",
                    confidence=0.95,
                    risk_tier="low",
                    last_assessed=now,
                ),
            ]
        )
        test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_session():
        with Session(test_engine) as test_session:
            yield test_session

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/registry/reasoning-gap",
        params={"max_age_days": 30, "limit": 50},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["missing_reasoning"] == 1
    assert payload["totals"]["missing_confidence"] == 1
    assert payload["totals"]["scored"] == 4
    assert isinstance(payload["examples"], list)
    assert all(isinstance(example, dict) for example in payload["examples"])
    print("PASS")