"""Data and computation layer for dispute resolution analytics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter(prefix="/api")


def _parse_proposed_axes(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _axis_names(value: Any) -> List[str]:
    """Extract axis names from the JSON representation used for proposals."""
    parsed = _parse_proposed_axes(value)
    if parsed is None:
        return []

    if isinstance(parsed, list):
        names: List[str] = []
        for item in parsed:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("axis", "name", "axis_name"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
                else:
                    names.extend(str(key) for key in item.keys())
        return names

    if isinstance(parsed, dict):
        # Some producers wrap the actual axis list in an "axes" member.
        if isinstance(parsed.get("axes"), (list, dict)):
            return _axis_names(parsed["axes"])
        return [str(key) for key in parsed.keys()]

    return []


def _resolution_hours(created_at: Any, resolved_at: Any) -> Optional[float]:
    if not isinstance(created_at, datetime) or not isinstance(resolved_at, datetime):
        return None
    delta = resolved_at - created_at
    return max(0.0, delta.total_seconds() / 3600.0)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def get_dispute_analytics(
    db: Session,
    status: Optional[str] = None,
    since_days: int = 90,
) -> Dict[str, Any]:
    """Compute dispute analytics from the real application dispute table."""
    statement = select(McpScoreDispute)
    if status:
        statement = statement.where(McpScoreDispute.status == status)
    if since_days >= 0:
        cutoff = datetime.utcnow() - timedelta(days=since_days)
        statement = statement.where(McpScoreDispute.created_at >= cutoff)

    disputes = list(db.execute(statement).scalars().all())

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    axis_proposal_histogram: Dict[str, int] = {}
    resolution_hours: List[float] = []
    resolved_count = 0

    for dispute in disputes:
        status_key = dispute.status or "unknown"
        by_status[status_key] = by_status.get(status_key, 0) + 1

        reason_key = dispute.reason_category or "unknown"
        by_reason_category[reason_key] = by_reason_category.get(reason_key, 0) + 1

        for axis_name in _axis_names(dispute.proposed_axes):
            axis_proposal_histogram[axis_name] = (
                axis_proposal_histogram.get(axis_name, 0) + 1
            )

        if dispute.resolved_at is not None:
            resolved_count += 1
            hours = _resolution_hours(dispute.created_at, dispute.resolved_at)
            if hours is not None:
                resolution_hours.append(hours)

    recent_disputes = sorted(
        disputes,
        key=lambda item: item.created_at or datetime.min,
        reverse=True,
    )[:20]

    recent = [
        {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "status": dispute.status,
            "reason_category": dispute.reason_category,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at,
        }
        for dispute in recent_disputes
    ]

    return {
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "median_resolution_hours": _median(resolution_hours),
        "resolution_rate": (
            float(resolved_count) / len(disputes) if disputes else 0.0
        ),
        "axis_proposal_histogram": axis_proposal_histogram,
        "recent": recent,
    }


@router.get("/disputes/analytics")
def dispute_resolution_analytics(
    db: Session = Depends(get_session),
    status: Optional[str] = Query(default=None),
    since_days: int = Query(default=90, ge=0),
) -> Dict[str, Any]:
    return get_dispute_analytics(db, status=status, since_days=since_days)


app = FastAPI(title="Dispute Resolution Analytics API")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)

    with Session(test_engine) as test_db:
        base_time = datetime.utcnow()
        seeded = [
            McpScoreDispute(
                server_id=1,
                submitted_by="analyst-1",
                proposed_overall_risk=0.8,
                proposed_axes={"security": 0.9, "quality": 0.7},
                reason_category="security",
                explanation="Security review",
                status="open",
                created_at=base_time - timedelta(days=2),
            ),
            McpScoreDispute(
                server_id=2,
                submitted_by="analyst-2",
                proposed_overall_risk=0.5,
                proposed_axes={"security": 0.6},
                reason_category="quality",
                explanation="Quality review",
                status="resolved",
                admin_note="Resolved after review",
                created_at=base_time - timedelta(days=4),
                resolved_at=base_time - timedelta(days=3),
            ),
            McpScoreDispute(
                server_id=3,
                submitted_by="analyst-3",
                proposed_overall_risk=0.3,
                proposed_axes={"quality": 0.4},
                reason_category="security",
                explanation="Additional security review",
                status="rejected",
                created_at=base_time - timedelta(days=6),
            ),
            McpScoreDispute(
                server_id=4,
                submitted_by="analyst-4",
                proposed_overall_risk=0.2,
                proposed_axes={"security": 0.2, "quality": 0.3},
                reason_category="quality",
                explanation="Additional quality review",
                status="resolved",
                admin_note="Resolved after review",
                created_at=base_time - timedelta(days=8),
                resolved_at=base_time - timedelta(days=5),
            ),
        ]
        test_db.add_all(seeded)
        test_db.commit()

        def override_session():
            yield test_db

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_session
        test_app.include_router(router)

        with TestClient(test_app) as client:
            response = client.get("/api/disputes/analytics")
            assert response.status_code == 200
            payload = response.json()
            assert payload["by_status"] == {
                "open": 1,
                "resolved": 2,
                "rejected": 1,
            }
            assert payload["by_reason_category"] == {"security": 2, "quality": 2}
            assert isinstance(payload["median_resolution_hours"], float)
            assert payload["median_resolution_hours"] >= 0
            assert 0 <= payload["resolution_rate"] <= 1
            assert len(payload["recent"]) == 4

    print("PASS")