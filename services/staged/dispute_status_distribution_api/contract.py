from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


TERMINAL_STATUSES = {"resolved", "rejected", "closed"}
router = APIRouter(prefix="/api")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_open(status: str) -> bool:
    return status.strip().lower() not in TERMINAL_STATUSES


@router.get("/disputes/status-distribution")
def get_dispute_status_distribution(
    session: Session = Depends(get_session),
) -> dict:
    disputes = session.execute(select(McpScoreDispute)).scalars().all()
    now = _utc_now()

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    oldest_open: dict[str, datetime] = {}
    open_ages: list[float] = []

    for dispute in disputes:
        status = str(dispute.status or "")
        reason_category = str(dispute.reason_category or "")
        proposed_risk = str(dispute.proposed_overall_risk or "")

        status_counts[status] += 1
        reason_counts[reason_category] += 1
        risk_counts[proposed_risk] += 1

        if not _is_open(status):
            continue

        created_at = _as_utc(dispute.created_at)
        if created_at is None:
            continue

        if status not in oldest_open or created_at < oldest_open[status]:
            oldest_open[status] = created_at

        age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
        open_ages.append(age_hours)

    by_status = []
    for status in sorted(status_counts):
        oldest = oldest_open.get(status)
        by_status.append(
            {
                "status": status,
                "count": status_counts[status],
                "oldest_open_at": oldest.isoformat() if oldest else None,
            }
        )

    return {
        "total": len(disputes),
        "by_status": by_status,
        "by_reason_category": [
            {"reason_category": reason, "count": count}
            for reason, count in sorted(reason_counts.items())
        ],
        "by_proposed_risk": [
            {"proposed_overall_risk": risk, "count": count}
            for risk, count in sorted(risk_counts.items())
        ],
        "open_median_age_hours": (
            float(median(open_ages)) if open_ages else None
        ),
        "stale_open_over_72h": sum(age > 72.0 for age in open_ages),
    }


def create_app() -> FastAPI:
    api = FastAPI()
    api.include_router(router)
    return api


app = create_app()


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpScoreDispute.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="user-1",
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="data_quality",
            status="open",
            explanation="Fixture row 1",
            admin_note=None,
            created_at=now.replace(microsecond=0),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-1",
            submitted_by="user-2",
            proposed_overall_risk="medium",
            proposed_axes={},
            reason_category="scoring",
            status="open",
            explanation="Fixture row 2",
            admin_note=None,
            created_at=now.replace(microsecond=0),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="user-1",
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="data_quality",
            status="open",
            explanation="Fixture row 3",
            admin_note=None,
            created_at=now.replace(microsecond=0),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="user-2",
            proposed_overall_risk="low",
            proposed_axes={},
            reason_category="scoring",
            status="open",
            explanation="Fixture row 4",
            admin_note=None,
            created_at=now.replace(microsecond=0),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="user-1",
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="data_quality",
            status="resolved",
            explanation="Fixture row 5",
            admin_note="Resolved by an administrator",
            created_at=now.replace(microsecond=0),
            resolved_at=now.replace(microsecond=0),
        ),
    ]

    with TestingSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/disputes/status-distribution")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5

        status_counts = {
            item["status"]: item["count"] for item in payload["by_status"]
        }
        assert status_counts["open"] == 4
        assert status_counts["resolved"] == 1
        assert isinstance(payload["open_median_age_hours"], (float, int))
        assert payload["stale_open_over_72h"] == 0
        assert len(payload["by_reason_category"]) == 2
        assert len(payload["by_proposed_risk"]) == 3
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()