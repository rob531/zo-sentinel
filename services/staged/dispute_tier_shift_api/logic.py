from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_TIER_RANKS = {
    "safe": 1,
    "minimal": 1,
    "very_low": 1,
    "low": 2,
    "moderate": 3,
    "medium": 3,
    "elevated": 4,
    "high": 4,
    "severe": 5,
    "critical": 5,
}

_AGE_BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _risk_rank(value: Any) -> Optional[Decimal]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float, Decimal)):
        try:
            rank = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return rank if rank.is_finite() else None

    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized or normalized in {"unknown", "none", "null", "n/a"}:
        return None

    if normalized in _TIER_RANKS:
        return Decimal(_TIER_RANKS[normalized])

    try:
        rank = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None

    return rank if rank.is_finite() else None


def _transition_direction(current_tier: Any, proposed_risk: Any) -> str:
    current_rank = _risk_rank(current_tier)
    proposed_rank = _risk_rank(proposed_risk)

    if current_rank is None or proposed_rank is None:
        return "unknown"

    if proposed_rank > current_rank:
        return "upgrade"
    if proposed_rank < current_rank:
        return "downgrade"
    return "unchanged"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(age_days: float) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def get_dispute_tier_shift(db: Session) -> Dict[str, Any]:
    """Summarize dispute tier transitions and age open disputes."""
    rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.reason_category,
            McpScoreDispute.server_id,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    ).all()

    directions: Counter[str] = Counter(
        {
            "upgrade": 0,
            "downgrade": 0,
            "unchanged": 0,
            "unknown": 0,
        }
    )
    reasons: Counter[str] = Counter()
    ageing_counts: Counter[str] = Counter({bucket: 0 for bucket in _AGE_BUCKETS})
    open_ages: List[float] = []
    now = datetime.now(timezone.utc)

    for (
        status,
        proposed_overall_risk,
        created_at,
        reason_category,
        _server_id,
        current_tier,
    ) in rows:
        direction = _transition_direction(current_tier, proposed_overall_risk)
        directions[direction] += 1

        category = str(reason_category).strip() if reason_category is not None else ""
        reasons[category or "unknown"] += 1

        if str(status or "").strip().lower() == "resolved":
            continue

        if created_at is not None:
            age_days = max(0.0, (now - _utc_datetime(created_at)).total_seconds() / 86400.0)
            open_ages.append(age_days)
            ageing_counts[_age_bucket(age_days)] += 1

    by_reason_category = [
        {"category": category, "count": int(count)}
        for category, count in sorted(
            reasons.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    median_age_days = float(median(open_ages)) if open_ages else None

    return {
        "total": len(rows),
        "by_direction": {
            "upgrade": int(directions["upgrade"]),
            "downgrade": int(directions["downgrade"]),
            "unchanged": int(directions["unchanged"]),
            "unknown": int(directions["unknown"]),
        },
        "by_reason_category": by_reason_category,
        "median_age_days": median_age_days,
        "ageing": [
            {"bucket": bucket, "count": int(ageing_counts[bucket])}
            for bucket in _AGE_BUCKETS
        ],
    }


compute_dispute_tier_shift = get_dispute_tier_shift
get_dispute_tier_shift_summary = get_dispute_tier_shift


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="upgrade-server", risk_tier="high"),
                McpServerRegistry(server_id="downgrade-server", risk_tier="medium"),
                McpServerRegistry(server_id="unchanged-server", risk_tier="low"),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    explanation="Upgrade request",
                    proposed_overall_risk="critical",
                    reason_category="new-evidence",
                    server_id="upgrade-server",
                    status="open",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    id=2,
                    explanation="Downgrade request",
                    proposed_overall_risk="low",
                    reason_category="false-positive",
                    server_id="downgrade-server",
                    status="open",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    id=3,
                    explanation="No tier change",
                    proposed_overall_risk="low",
                    reason_category="clarification",
                    server_id="unchanged-server",
                    status="open",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    id=4,
                    explanation="Unknown current tier",
                    proposed_overall_risk="high",
                    reason_category="stale-registry",
                    server_id="missing-server",
                    status="resolved",
                    resolved_at=datetime.now(timezone.utc),
                    submitted_by="self-test",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/tier-shift")
    def tier_shift_endpoint(session: Session = Depends(get_session)):
        return get_dispute_tier_shift(session)

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/tier-shift")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["by_direction"] == {
            "upgrade": 1,
            "downgrade": 1,
            "unchanged": 1,
            "unknown": 1,
        }
        assert sum(item["count"] for item in payload["ageing"]) == 3

    print("PASS")