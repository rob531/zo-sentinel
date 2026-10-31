from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


RESOLVED_STATUSES = frozenset({"resolved", "closed", "dismissed"})


class ReasonRollupItem(BaseModel):
    reason_category: str
    total: int
    open: int
    resolved: int
    avg_resolution_hours: Optional[float]


class ReasonRollupResponse(BaseModel):
    days: int
    total: int
    by_status: Dict[str, int]
    by_reason: List[ReasonRollupItem]
    proposed_tier_mix: Dict[str, int]


router = APIRouter(prefix="/api", tags=["disputes"])


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    else:
        return None

    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def compute_reason_rollup(session: Session, days: int = 30) -> ReasonRollupResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = session.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        ).where(McpScoreDispute.created_at >= cutoff)
    ).all()

    by_status: Dict[str, int] = {}
    by_reason_totals: Dict[str, int] = {}
    by_reason_open: Dict[str, int] = {}
    by_reason_resolved: Dict[str, int] = {}
    resolution_hours: Dict[str, List[float]] = {}
    proposed_tier_mix: Dict[str, int] = {}

    for row in rows:
        status = str(row.status) if row.status is not None else "unknown"
        reason_category = (
            str(row.reason_category)
            if row.reason_category is not None
            else "uncategorized"
        )
        proposed_overall_risk = (
            str(row.proposed_overall_risk)
            if row.proposed_overall_risk is not None
            else "unknown"
        )

        by_status[status] = by_status.get(status, 0) + 1
        by_reason_totals[reason_category] = by_reason_totals.get(reason_category, 0) + 1
        proposed_tier_mix[proposed_overall_risk] = (
            proposed_tier_mix.get(proposed_overall_risk, 0) + 1
        )

        normalized_status = status.strip().lower()
        if normalized_status == "open":
            by_reason_open[reason_category] = by_reason_open.get(reason_category, 0) + 1

        if normalized_status in RESOLVED_STATUSES and row.resolved_at is not None:
            by_reason_resolved[reason_category] = (
                by_reason_resolved.get(reason_category, 0) + 1
            )

            created_at = _as_datetime(row.created_at)
            resolved_at = _as_datetime(row.resolved_at)
            if created_at is not None and resolved_at is not None:
                hours = (resolved_at - created_at).total_seconds() / 3600.0
                resolution_hours.setdefault(reason_category, []).append(hours)

    by_reason = []
    for reason_category, total in sorted(
        by_reason_totals.items(), key=lambda item: (-item[1], item[0])
    ):
        hours = resolution_hours.get(reason_category, [])
        by_reason.append(
            ReasonRollupItem(
                reason_category=reason_category,
                total=total,
                open=by_reason_open.get(reason_category, 0),
                resolved=by_reason_resolved.get(reason_category, 0),
                avg_resolution_hours=(sum(hours) / len(hours)) if hours else None,
            )
        )

    return ReasonRollupResponse(
        days=days,
        total=len(rows),
        by_status=by_status,
        by_reason=by_reason,
        proposed_tier_mix=proposed_tier_mix,
    )


@router.get(
    "/disputes/reason-rollup",
    response_model=ReasonRollupResponse,
)
def get_reason_rollup(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> ReasonRollupResponse:
    return compute_reason_rollup(session, days)


if __name__ == "__main__":
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
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)

    now = datetime.utcnow()
    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    submitted_by="user-1",
                    proposed_overall_risk="high",
                    proposed_axes='{"a": 1}',
                    reason_category="billing",
                    explanation="Billing mismatch",
                    status="open",
                    created_at=now - timedelta(days=2),
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    submitted_by="user-2",
                    proposed_overall_risk="medium",
                    proposed_axes='{"b": 1}',
                    reason_category="scope",
                    explanation="Scope mismatch",
                    status="open",
                    created_at=now - timedelta(days=3),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-3",
                    submitted_by="user-3",
                    proposed_overall_risk="low",
                    proposed_axes='{"c": 1}',
                    reason_category="billing",
                    explanation="Resolved billing mismatch",
                    status="resolved",
                    created_at=now - timedelta(days=4),
                    resolved_at=now - timedelta(days=4) + timedelta(hours=2),
                ),
                McpScoreDispute(
                    id=4,
                    server_id="server-4",
                    submitted_by="user-4",
                    proposed_overall_risk="critical",
                    proposed_axes='{"d": 1}',
                    reason_category="scope",
                    explanation="Resolved without a resolution timestamp",
                    status="resolved",
                    created_at=now - timedelta(days=5),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    def override_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/disputes/reason-rollup?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert "open" in payload["by_status"]
    assert "resolved" in payload["by_status"]
    billing = next(
        item for item in payload["by_reason"] if item["reason_category"] == "billing"
    )
    assert billing["avg_resolution_hours"] is not None
    print("PASS")