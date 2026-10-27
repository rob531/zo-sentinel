from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class SubmitterLeaderboardEntry(BaseModel):
    submitted_by: Optional[str]
    total: int
    open: int
    resolved: int
    pending: int
    last_dispute_at: Optional[datetime]
    median_resolution_hours: Optional[float]


class SubmitterLeaderboardResponse(BaseModel):
    total_submitters: int
    submitters: List[SubmitterLeaderboardEntry]


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    created = created_at
    resolved = resolved_at

    if created.tzinfo is not None and resolved.tzinfo is None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    elif created.tzinfo is None and resolved.tzinfo is not None:
        resolved = resolved.astimezone(timezone.utc).replace(tzinfo=None)
    elif created.tzinfo is not None and resolved.tzinfo is not None:
        created = created.astimezone(timezone.utc)
        resolved = resolved.astimezone(timezone.utc)

    return max(0.0, (resolved - created).total_seconds() / 3600.0)


def get_dispute_submitter_leaderboard(
    session: Session,
    limit: int = 50,
) -> Dict[str, object]:
    bounded_limit = max(1, min(int(limit), 200))
    statement = select(
        McpScoreDispute.submitted_by,
        McpScoreDispute.status,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    )
    rows = session.execute(statement).all()

    grouped: Dict[Optional[str], Dict[str, object]] = {}

    for submitted_by, status, created_at, resolved_at in rows:
        entry = grouped.setdefault(
            submitted_by,
            {
                "total": 0,
                "open": 0,
                "resolved": 0,
                "pending": 0,
                "last_dispute_at": None,
                "resolution_hours": [],
            },
        )
        entry["total"] += 1

        normalized_status = str(status or "").strip().lower()
        if normalized_status == "open":
            entry["open"] += 1
        elif normalized_status == "resolved":
            entry["resolved"] += 1
        elif normalized_status == "pending":
            entry["pending"] += 1

        if created_at is not None:
            last_dispute_at = entry["last_dispute_at"]
            if last_dispute_at is None or created_at > last_dispute_at:
                entry["last_dispute_at"] = created_at

        if (
            normalized_status == "resolved"
            and created_at is not None
            and resolved_at is not None
        ):
            entry["resolution_hours"].append(
                _resolution_hours(created_at, resolved_at)
            )

    submitters: List[SubmitterLeaderboardEntry] = []
    for submitted_by, entry in grouped.items():
        durations = entry["resolution_hours"]
        median_resolution_hours = (
            round(float(median(durations)), 2) if durations else None
        )
        submitters.append(
            SubmitterLeaderboardEntry(
                submitted_by=submitted_by,
                total=int(entry["total"]),
                open=int(entry["open"]),
                resolved=int(entry["resolved"]),
                pending=int(entry["pending"]),
                last_dispute_at=entry["last_dispute_at"],
                median_resolution_hours=median_resolution_hours,
            )
        )

    submitters.sort(
        key=lambda item: (-item.total, item.submitted_by or "")
    )
    limited_submitters = submitters[:bounded_limit]

    return {
        "total_submitters": len(submitters),
        "submitters": limited_submitters,
    }


get_submitter_leaderboard = get_dispute_submitter_leaderboard


router = APIRouter()


@router.get(
    "/disputes/submitter-leaderboard",
    response_model=SubmitterLeaderboardResponse,
)
def submitter_leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    return get_dispute_submitter_leaderboard(session=session, limit=limit)


app = FastAPI(title="score_dispute_submitter_leaderboard_api")
app.include_router(router, prefix="/api")


def _self_test() -> None:
    from datetime import timedelta

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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    base = datetime(2025, 1, 1, 0, 0, 0)
    rows = [
        McpScoreDispute(
            id=1,
            server_id=1,
            submitted_by="alice@example.com",
            proposed_overall_risk=0.8,
            proposed_axes={"security": 0.9},
            reason_category="incorrect_score",
            status="open",
            explanation="First dispute",
            admin_note=None,
            created_at=base,
            resolved_at=None,
        ),
        McpScoreDispute(
            id=2,
            server_id=1,
            submitted_by="alice@example.com",
            proposed_overall_risk=0.7,
            proposed_axes={"security": 0.8},
            reason_category="incorrect_score",
            status="resolved",
            explanation="Second dispute",
            admin_note="Adjusted",
            created_at=base + timedelta(hours=1),
            resolved_at=base + timedelta(hours=3),
        ),
        McpScoreDispute(
            id=3,
            server_id=2,
            submitted_by="alice@example.com",
            proposed_overall_risk=0.6,
            proposed_axes={"security": 0.7},
            reason_category="missing_signal",
            status="pending",
            explanation="Third dispute",
            admin_note=None,
            created_at=base + timedelta(days=1),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id=2,
            submitted_by="bob@example.com",
            proposed_overall_risk=0.5,
            proposed_axes={"security": 0.6},
            reason_category="incorrect_score",
            status="resolved",
            explanation="Fourth dispute",
            admin_note="Corrected",
            created_at=base + timedelta(days=2),
            resolved_at=base + timedelta(days=2, hours=4),
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/submitter-leaderboard")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_submitters"] == 2
        assert len(payload["submitters"]) == 2
        assert payload["submitters"][0]["submitted_by"] == "alice@example.com"
        assert payload["submitters"][0]["total"] == 3
        assert all(
            entry["total"] >= entry["open"]
            for entry in payload["submitters"]
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()