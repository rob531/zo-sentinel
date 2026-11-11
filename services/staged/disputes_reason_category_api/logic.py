from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _resolution_hours(created_at: Any, resolved_at: Any) -> Optional[float]:
    if not isinstance(created_at, datetime) or not isinstance(resolved_at, datetime):
        return None

    try:
        created = _utc_naive(created_at)
        resolved = _utc_naive(resolved_at)
        return max(0.0, (resolved - created).total_seconds() / 3600.0)
    except (TypeError, ValueError, OverflowError):
        return None


def _tier_value(row: McpScoreDispute) -> Optional[str]:
    tier = row.proposed_overall_risk
    if tier is None:
        return None
    if isinstance(tier, dict):
        tier = tier.get("tier") or tier.get("overall_risk")
    if tier is None:
        return None
    return str(tier)


def get_reason_categories(
    session: Session = Depends(get_session),
    days: int = 90,
) -> Dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    disputes = (
        session.query(McpScoreDispute)
        .filter(McpScoreDispute.created_at >= cutoff)
        .all()
    )

    grouped: Dict[str, Dict[str, Any]] = {}

    for dispute in disputes:
        category = dispute.reason_category
        if category is None:
            category = "uncategorized"
        else:
            category = str(category)

        if category not in grouped:
            grouped[category] = {
                "reason_category": category,
                "total": 0,
                "open": 0,
                "resolved": 0,
                "rejected": 0,
                "avg_resolution_hours": None,
                "proposed_tiers": {},
            }

        item = grouped[category]
        item["total"] += 1

        if dispute.status == "rejected":
            item["rejected"] += 1
        elif dispute.status not in ("resolved", "rejected"):
            item["open"] += 1

        if dispute.resolved_at is not None:
            item["resolved"] += 1
            hours = _resolution_hours(dispute.created_at, dispute.resolved_at)
            if hours is not None:
                if item["avg_resolution_hours"] is None:
                    item["avg_resolution_hours"] = 0.0
                item["avg_resolution_hours"] += hours

        tier = _tier_value(dispute)
        if tier is not None:
            item["proposed_tiers"][tier] = item["proposed_tiers"].get(tier, 0) + 1

    categories: List[Dict[str, Any]] = []
    for category in sorted(grouped.values(), key=lambda value: value["reason_category"]):
        average = category["avg_resolution_hours"]
        if average is not None:
            category["avg_resolution_hours"] = average / category["resolved"]
        categories.append(category)

    return {
        "days": days,
        "total_disputes": len(disputes),
        "categories": categories,
    }


get_reason_category_summary = get_reason_categories


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
    SessionLocal = sessionmaker(bind=engine)

    local_app = FastAPI()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/disputes/reason-categories")
    def reason_categories(
        days: int = 90,
        session: Session = Depends(get_session),
    ):
        return get_reason_categories(session=session, days=days)

    now = datetime.utcnow()
    rows = [
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="identity",
            explanation="",
            status="open",
            admin_note="",
            created_at=now - timedelta(days=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=2,
            submitted_by=1,
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="identity",
            explanation="",
            status="open",
            admin_note="",
            created_at=now - timedelta(days=3),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=3,
            submitted_by=1,
            proposed_overall_risk="medium",
            proposed_axes={},
            reason_category="availability",
            explanation="",
            status="resolved",
            admin_note="",
            created_at=now - timedelta(days=4),
            resolved_at=now - timedelta(days=4) + timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id=4,
            submitted_by=1,
            proposed_overall_risk="medium",
            proposed_axes={},
            reason_category="availability",
            explanation="",
            status="resolved",
            admin_note="",
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=5) + timedelta(hours=4),
        ),
        McpScoreDispute(
            server_id=5,
            submitted_by=1,
            proposed_overall_risk="low",
            proposed_axes={},
            reason_category="configuration",
            explanation="",
            status="rejected",
            admin_note="",
            created_at=now - timedelta(days=6),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=6,
            submitted_by=1,
            proposed_overall_risk="low",
            proposed_axes={},
            reason_category="configuration",
            explanation="",
            status="rejected",
            admin_note="",
            created_at=now - timedelta(days=7),
            resolved_at=None,
        ),
    ]

    db = SessionLocal()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()

    response = TestClient(local_app).get(
        "/api/disputes/reason-categories",
        params={"days": 90},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["categories"]) == 3
    by_category = {
        category["reason_category"]: category for category in payload["categories"]
    }
    assert abs(by_category["availability"]["avg_resolution_hours"] - 3.0) <= 0.001
    assert by_category["identity"]["open"] == 2
    print("PASS")