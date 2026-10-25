from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

_STATUS_KEYS = ("open", "in_review", "resolved", "rejected")
_UNRESOLVED_STATUSES = {"open", "in_review"}


def _normalise_limit(limit: Optional[int]) -> int:
    if limit is None:
        return 500
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 500
    return max(0, min(value, 500))


def _as_datetime(value: Any):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value
    if isinstance(value, str):
        try:
            return __import__("datetime").datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None
    return None


def _utc_naive(value: Any):
    from datetime import timezone

    value = _as_datetime(value)
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_status_mix(
    session: Session,
    status: Optional[str] = None,
    limit: Optional[int] = 500,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    if status is None:
        status = status_filter

    row_limit = _normalise_limit(limit)
    statement = select(McpScoreDispute)

    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    statement = statement.order_by(McpScoreDispute.created_at.asc()).limit(row_limit)
    rows = list(session.execute(statement).scalars().all())

    by_status = {key: 0 for key in _STATUS_KEYS}
    reason_counts: Dict[str, int] = {}
    resolution_hours: List[float] = []
    unresolved_created_at: List[Any] = []

    for dispute in rows:
        dispute_status = getattr(dispute, "status", None)
        if dispute_status in by_status:
            by_status[dispute_status] += 1

        reason_category = getattr(dispute, "reason_category", None)
        if reason_category is not None:
            reason_counts[reason_category] = reason_counts.get(reason_category, 0) + 1

        created_at = _as_datetime(getattr(dispute, "created_at", None))
        resolved_at = _as_datetime(getattr(dispute, "resolved_at", None))

        if resolved_at is not None and created_at is not None:
            start = _utc_naive(created_at)
            end = _utc_naive(resolved_at)
            if start is not None and end is not None:
                resolution_hours.append(max(0.0, (end - start).total_seconds() / 3600.0))

        if dispute_status in _UNRESOLVED_STATUSES and created_at is not None:
            unresolved_created_at.append(created_at)

    if resolution_hours:
        ordered = sorted(resolution_hours)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            median_resolution_hours = float(ordered[middle])
        else:
            median_resolution_hours = float(
                (ordered[middle - 1] + ordered[middle]) / 2.0
            )
    else:
        median_resolution_hours = 0.0

    if unresolved_created_at:
        oldest = min(unresolved_created_at)
        unresolved_oldest_created_at = oldest.isoformat()
    else:
        unresolved_oldest_created_at = None

    by_reason_category = [
        {"reason_category": reason, "count": reason_counts[reason]}
        for reason in sorted(reason_counts)
    ]

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "median_resolution_hours": median_resolution_hours,
        "unresolved_oldest_created_at": unresolved_oldest_created_at,
    }


def get_dispute_status_mix(
    session: Session,
    status: Optional[str] = None,
    limit: Optional[int] = 500,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    return get_status_mix(
        session=session,
        status=status,
        limit=limit,
        status_filter=status_filter,
    )


def get_status_mix_logic(
    session: Session,
    status: Optional[str] = None,
    limit: Optional[int] = 500,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    return get_status_mix(
        session=session,
        status=status,
        limit=limit,
        status_filter=status_filter,
    )


def compute_status_mix(
    session: Session,
    status: Optional[str] = None,
    limit: Optional[int] = 500,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    return get_status_mix(
        session=session,
        status=status,
        limit=limit,
        status_filter=status_filter,
    )


def _self_test() -> None:
    from datetime import datetime, timedelta

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
    McpScoreDispute.__table__.create(bind=engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    base = datetime(2024, 1, 1, 12, 0, 0)
    seed_rows = [
        {
            "id": 1,
            "server_id": 1,
            "submitted_by": "tester",
            "proposed_overall_risk": 0.8,
            "proposed_axes": {"security": 0.8},
            "reason_category": "model_error",
            "explanation": "one",
            "status": "open",
            "admin_note": None,
            "created_at": base,
            "resolved_at": None,
        },
        {
            "id": 2,
            "server_id": 1,
            "submitted_by": "tester",
            "proposed_overall_risk": 0.7,
            "proposed_axes": {"security": 0.7},
            "reason_category": "data_error",
            "explanation": "two",
            "status": "open",
            "admin_note": None,
            "created_at": base + timedelta(hours=1),
            "resolved_at": None,
        },
        {
            "id": 3,
            "server_id": 1,
            "submitted_by": "tester",
            "proposed_overall_risk": 0.6,
            "proposed_axes": {"security": 0.6},
            "reason_category": "model_error",
            "explanation": "three",
            "status": "in_review",
            "admin_note": None,
            "created_at": base + timedelta(hours=2),
            "resolved_at": None,
        },
        {
            "id": 4,
            "server_id": 1,
            "submitted_by": "tester",
            "proposed_overall_risk": 0.5,
            "proposed_axes": {"security": 0.5},
            "reason_category": "data_error",
            "explanation": "four",
            "status": "resolved",
            "admin_note": "done",
            "created_at": base + timedelta(hours=3),
            "resolved_at": base + timedelta(hours=5),
        },
        {
            "id": 5,
            "server_id": 1,
            "submitted_by": "tester",
            "proposed_overall_risk": 0.4,
            "proposed_axes": {"security": 0.4},
            "reason_category": "policy_error",
            "explanation": "five",
            "status": "rejected",
            "admin_note": "not applicable",
            "created_at": base + timedelta(hours=4),
            "resolved_at": base + timedelta(hours=6),
        },
    ]

    db = TestingSession()
    try:
        db.query(McpScoreDispute).delete()
        for row in seed_rows:
            db.add(McpScoreDispute(**row))
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/disputes/status-mix")
    def status_mix_endpoint(
        status: Optional[str] = None,
        limit: int = 500,
        session: Session = Depends(get_session),
    ):
        return get_status_mix(session=session, status=status, limit=limit)

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/disputes/status-mix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["by_status"] == {
        "open": 2,
        "in_review": 1,
        "resolved": 1,
        "rejected": 1,
    }
    assert isinstance(payload["median_resolution_hours"], float)
    assert payload["median_resolution_hours"] >= 0
    assert isinstance(payload["unresolved_oldest_created_at"], str)
    datetime.fromisoformat(payload["unresolved_oldest_created_at"])
    print("PASS")


if __name__ == "__main__":
    _self_test()