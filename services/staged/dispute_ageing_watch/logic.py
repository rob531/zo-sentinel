"""Logic for the dispute ageing watch service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_RESOLVED_STATUSES = {"RESOLVED", "REJECTED", "CLOSED"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(age_hours: float) -> str:
    if age_hours < 24:
        return "lt_24h"
    if age_hours <= 7 * 24:
        return "d1_7"
    if age_hours <= 30 * 24:
        return "d8_30"
    return "gt_30d"


def _normalized_tier(value: object) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    return normalized or None


def get_dispute_ageing(db: Session) -> Dict[str, object]:
    """Build an ageing view of disputes whose status is not resolved."""
    now = _utc_now()
    rows = db.execute(
        select(McpScoreDispute, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    ).all()

    open_total = 0
    by_age_bucket = {
        "lt_24h": 0,
        "d1_7": 0,
        "d8_30": 0,
        "gt_30d": 0,
    }
    by_status: Dict[str, int] = {}
    tier_change_proposed = 0
    oldest: Optional[tuple[datetime, McpScoreDispute, Optional[McpServerRegistry]]] = None

    for dispute, server in rows:
        status = str(dispute.status or "UNKNOWN")
        if status.strip().upper() in _RESOLVED_STATUSES:
            continue

        open_total += 1
        by_status[status] = by_status.get(status, 0) + 1

        current_tier = _normalized_tier(server.risk_tier) if server is not None else None
        proposed_tier = _normalized_tier(dispute.proposed_overall_risk)
        if (
            current_tier is not None
            and proposed_tier is not None
            and current_tier != proposed_tier
        ):
            tier_change_proposed += 1

        if dispute.created_at is None:
            continue

        created_at = _as_utc(dispute.created_at)
        age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
        by_age_bucket[_age_bucket(age_hours)] += 1

        if oldest is None or created_at < oldest[0]:
            oldest = (created_at, dispute, server)

    oldest_open: Optional[Dict[str, object]] = None
    if oldest is not None:
        created_at, dispute, server = oldest
        age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
        oldest_open = {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "server_name": (
                server.name
                if server is not None and server.name
                else dispute.server_id
            ),
            "created_at": dispute.created_at,
            "age_hours": round(age_hours, 2),
            "reason_category": dispute.reason_category,
        }

    return {
        "open_total": open_total,
        "by_age_bucket": by_age_bucket,
        "by_status": by_status,
        "oldest_open": oldest_open,
        "tier_change_proposed": tier_change_proposed,
    }


def get_dispute_ageing_view(db: Session) -> Dict[str, object]:
    return get_dispute_ageing(db)


def calculate_dispute_ageing(db: Session) -> Dict[str, object]:
    return get_dispute_ageing(db)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)

    session = TestingSession()
    now = datetime.now(timezone.utc)
    session.add(
        McpServerRegistry(
            server_id="ageing-server",
            name="Ageing Test Server",
            risk_tier="low",
        )
    )
    session.add_all(
        [
            McpScoreDispute(
                server_id="ageing-server",
                submitted_by="tester",
                proposed_overall_risk="low",
                reason_category="recent",
                status="OPEN",
                explanation="Open dispute",
                created_at=now - timedelta(hours=2),
            ),
            McpScoreDispute(
                server_id="ageing-server",
                submitted_by="tester",
                proposed_overall_risk="medium",
                reason_category="week",
                status="PENDING",
                explanation="Pending dispute",
                created_at=now - timedelta(days=3),
            ),
            McpScoreDispute(
                server_id="ageing-server",
                submitted_by="tester",
                proposed_overall_risk="high",
                reason_category="month",
                status="IN_REVIEW",
                explanation="Dispute in review",
                created_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                server_id="ageing-server",
                submitted_by="tester",
                proposed_overall_risk="high",
                reason_category="resolved",
                status="RESOLVED",
                explanation="Resolved dispute",
                created_at=now - timedelta(days=40),
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/ageing")
    def ageing_route(db: Session = Depends(get_session)):
        return get_dispute_ageing(db)

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/ageing")
        assert response.status_code == 200
        payload = response.json()
        assert payload["open_total"] == 3
        assert payload["by_age_bucket"]["d8_30"] == 1
        assert payload["by_age_bucket"]["gt_30d"] == 0
        assert payload["oldest_open"]["reason_category"] == "month"

    print("PASS")