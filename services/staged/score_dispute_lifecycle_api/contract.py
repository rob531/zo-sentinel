from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

app = FastAPI(title="Score Dispute Lifecycle API")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _median(values: List[float]) -> float:
    return _percentile(values, 0.5)


@app.get("/api/disputes/lifecycle")
def dispute_lifecycle(session: Session = Depends(get_session)) -> Dict[str, Any]:
    rows = list(session.execute(select(McpScoreDispute)).scalars())
    now = datetime.now(timezone.utc)

    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    open_ages: List[float] = []
    resolution_hours: List[float] = []
    oldest_open: List[Dict[str, Any]] = []

    for dispute in rows:
        status = dispute.status or "unknown"
        reason = dispute.reason_category or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1

        created_at = _utc(dispute.created_at)
        if status != "resolved":
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
            open_ages.append(age_days)
            oldest_open.append(
                {
                    "id": dispute.id,
                    "server_id": dispute.server_id,
                    "created_at": dispute.created_at.isoformat()
                    if hasattr(dispute.created_at, "isoformat")
                    else dispute.created_at,
                    "age_days": round(age_days, 6),
                }
            )

        if dispute.resolved_at is not None:
            resolved_at = _utc(dispute.resolved_at)
            latency_hours = max(
                0.0, (resolved_at - created_at).total_seconds() / 3600.0
            )
            resolution_hours.append(latency_hours)

    oldest_open.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason": by_reason,
        "open_age_days": {
            "median": round(_median(open_ages), 6),
            "p90": round(_percentile(open_ages, 0.9), 6),
            "max": round(max(open_ages), 6) if open_ages else 0.0,
        },
        "resolution_latency_hours": {
            "median": round(_median(resolution_hours), 6),
            "p90": round(_percentile(resolution_hours, 0.9), 6),
        },
        "oldest_open": oldest_open,
    }


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [
        McpScoreDispute(
            id=1,
            server_id="server-a",
            submitted_by="user-a",
            proposed_overall_risk=0.8,
            proposed_axes={"maliciousness": 0.9},
            reason_category="ambiguity",
            explanation="Ambiguous score",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=10),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=2,
            server_id="server-b",
            submitted_by="user-b",
            proposed_overall_risk=0.6,
            proposed_axes={"maliciousness": 0.7},
            reason_category="data_quality",
            explanation="Source data issue",
            status="under_review",
            admin_note=None,
            created_at=now - timedelta(days=20),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=3,
            server_id="server-c",
            submitted_by="user-c",
            proposed_overall_risk=0.4,
            proposed_axes={"maliciousness": 0.5},
            reason_category="ambiguity",
            explanation="Additional ambiguity",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=30),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id="server-d",
            submitted_by="user-d",
            proposed_overall_risk=0.7,
            proposed_axes={"maliciousness": 0.8},
            reason_category="data_quality",
            explanation="Resolved data issue",
            status="resolved",
            admin_note="Corrected",
            created_at=now - timedelta(days=40),
            resolved_at=now - timedelta(days=40) + timedelta(hours=5),
        ),
        McpScoreDispute(
            id=5,
            server_id="server-e",
            submitted_by="user-e",
            proposed_overall_risk=0.5,
            proposed_axes={"maliciousness": 0.6},
            reason_category="ambiguity",
            explanation="Resolved ambiguity",
            status="resolved",
            admin_note="Accepted",
            created_at=now - timedelta(days=50),
            resolved_at=now - timedelta(days=50) + timedelta(hours=8),
        ),
        McpScoreDispute(
            id=6,
            server_id="server-f",
            submitted_by="user-f",
            proposed_overall_risk=0.3,
            proposed_axes={"maliciousness": 0.4},
            reason_category="other",
            explanation="Resolved other issue",
            status="resolved",
            admin_note="Closed",
            created_at=now - timedelta(days=60),
            resolved_at=now - timedelta(days=60) + timedelta(hours=11),
        ),
    ]
    session.add_all(rows)
    session.commit()

    def override_get_session():
        yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.add_api_route(
        "/api/disputes/lifecycle",
        dispute_lifecycle,
        methods=["GET"],
    )

    try:
        response = TestClient(test_app).get("/api/disputes/lifecycle")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        assert payload["by_status"]["resolved"] == 3
        assert payload["by_reason"]["ambiguity"] == 3
        assert abs(payload["open_age_days"]["median"] - 20.0) < 0.2
        assert abs(payload["resolution_latency_hours"]["median"] - 8.0) < 0.01
        assert abs(payload["resolution_latency_hours"]["p90"] - 10.4) < 0.01
        assert payload["oldest_open"][0]["id"] == 3
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()