from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpScoreDispute


KNOWN_STATUSES = frozenset(
    {
        "open",
        "resolved",
        "in_progress",
        "under_review",
        "closed",
        "rejected",
        "withdrawn",
    }
)


class ReasonCategoryLatency(BaseModel):
    reason_category: str
    count: int
    median_resolution_hours: Optional[float]


class DisputeLatencyResponse(BaseModel):
    days: int
    status: Optional[str]
    by_status: Dict[str, int]
    median_resolution_hours: Optional[float]
    p90_resolution_hours: Optional[float]
    stale_open: int
    by_reason_category: List[ReasonCategoryLatency]


def _as_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _dispute_latency(
    session: Session,
    days: int,
    status: Optional[str],
) -> DisputeLatencyResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = select(McpScoreDispute).where(McpScoreDispute.created_at >= cutoff)
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    disputes = list(session.execute(statement).scalars().all())

    by_status: Dict[str, int] = {}
    resolution_hours: List[float] = []
    reason_values: Dict[str, List[float]] = {}
    reason_counts: Dict[str, int] = {}
    stale_open = 0

    for dispute in disputes:
        dispute_status = str(dispute.status or "unknown")
        by_status[dispute_status] = by_status.get(dispute_status, 0) + 1

        created_at = _as_utc(dispute.created_at)
        resolved_at = _as_utc(dispute.resolved_at)
        category = str(dispute.reason_category or "unknown")
        reason_counts[category] = reason_counts.get(category, 0) + 1

        if dispute_status == "open":
            if created_at is not None:
                age = datetime.now(timezone.utc) - created_at
                if age > timedelta(days=7):
                    stale_open += 1

        if dispute_status == "resolved" and created_at is not None and resolved_at is not None:
            hours = (resolved_at - created_at).total_seconds() / 3600.0
            if hours >= 0:
                resolution_hours.append(hours)
                reason_values.setdefault(category, []).append(hours)

    by_reason_category = [
        ReasonCategoryLatency(
            reason_category=category,
            count=reason_counts[category],
            median_resolution_hours=_median(reason_values.get(category, [])),
        )
        for category in sorted(reason_counts)
    ]

    return DisputeLatencyResponse(
        days=days,
        status=status,
        by_status=by_status,
        median_resolution_hours=_median(resolution_hours),
        p90_resolution_hours=_percentile(resolution_hours, 0.90),
        stale_open=stale_open,
        by_reason_category=by_reason_category,
    )


def create_app() -> FastAPI:
    api = FastAPI(title="Dispute Resolution Latency API")

    @api.get(
        "/api/disputes/latency",
        response_model=DisputeLatencyResponse,
    )
    def dispute_resolution_latency(
        days: int = Query(30, ge=1, le=3650),
        status: Optional[str] = Query(None),
        session: Session = Depends(get_session),
    ):
        if status is not None and status not in KNOWN_STATUSES:
            return JSONResponse(
                status_code=400,
                content={"error": "unknown_status"},
            )
        return _dispute_latency(session, days, status)

    return api


app = create_app()


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    testing_session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="server-resolved-10",
                submitted_by="reviewer",
                proposed_overall_risk=0.8,
                proposed_axes={"security": 0.8},
                reason_category="security",
                explanation="resolved after ten hours",
                status="resolved",
                admin_note="reviewed",
                created_at=now - timedelta(days=2),
                resolved_at=now - timedelta(days=2, hours=-10),
            ),
            McpScoreDispute(
                id=2,
                server_id="server-resolved-30",
                submitted_by="reviewer",
                proposed_overall_risk=0.9,
                proposed_axes={"security": 0.9},
                reason_category="security",
                explanation="resolved after thirty hours",
                status="resolved",
                admin_note="reviewed",
                created_at=now - timedelta(days=3),
                resolved_at=now - timedelta(days=3, hours=-30),
            ),
            McpScoreDispute(
                id=3,
                server_id="server-open-old",
                submitted_by="reviewer",
                proposed_overall_risk=0.7,
                proposed_axes={"quality": 0.7},
                reason_category="quality",
                explanation="still awaiting review",
                status="open",
                admin_note=None,
                created_at=now - timedelta(days=10),
                resolved_at=None,
            ),
            McpScoreDispute(
                id=4,
                server_id="server-open-recent",
                submitted_by="reviewer",
                proposed_overall_risk=0.4,
                proposed_axes={"quality": 0.4},
                reason_category="quality",
                explanation="recently opened",
                status="open",
                admin_note=None,
                created_at=now - timedelta(days=1),
                resolved_at=None,
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        from fastapi.testclient import TestClient

        with TestClient(that_app) as client:
            response = client.get("/api/disputes/latency", params={"days": 90})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["by_status"]["resolved"] == 2, payload
            assert payload["median_resolution_hours"] == 20.0, payload
            assert payload["stale_open"] == 1, payload
            assert len(payload["by_reason_category"]) >= 1, payload

            invalid = client.get(
                "/api/disputes/latency",
                params={"days": 90, "status": "not-a-status"},
            )
            assert invalid.status_code == 400, invalid.text
            assert invalid.json() == {"error": "unknown_status"}, invalid.text
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()