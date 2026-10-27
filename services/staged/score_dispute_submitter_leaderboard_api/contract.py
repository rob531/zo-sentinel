from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class SubmitterLeaderboardEntry(BaseModel):
    submitted_by: str
    total: int
    open: int
    resolved: int
    pending: int
    last_dispute_at: Optional[datetime]
    median_resolution_hours: Optional[float]


class SubmitterLeaderboardResponse(BaseModel):
    total_submitters: int
    submitters: List[SubmitterLeaderboardEntry]


router = APIRouter()


@router.get(
    "/disputes/submitter-leaderboard",
    response_model=SubmitterLeaderboardResponse,
)
def get_submitter_leaderboard(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> SubmitterLeaderboardResponse:
    rows = session.execute(
        select(
            McpScoreDispute.submitted_by,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()

    groups: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        submitted_by = row.submitted_by
        if not submitted_by:
            continue

        group = groups.setdefault(
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

        group["total"] += 1
        status = (row.status or "").lower()
        if status == "open":
            group["open"] += 1
        elif status == "resolved":
            group["resolved"] += 1
        elif status == "pending":
            group["pending"] += 1

        created_at = row.created_at
        if created_at is not None:
            current_last = group["last_dispute_at"]
            if current_last is None or created_at > current_last:
                group["last_dispute_at"] = created_at

        if row.resolved_at is not None and created_at is not None:
            elapsed = row.resolved_at - created_at
            group["resolution_hours"].append(elapsed.total_seconds() / 3600.0)

    ordered = sorted(
        groups.items(),
        key=lambda item: (-item[1]["total"], item[0]),
    )

    entries: List[SubmitterLeaderboardEntry] = []
    for submitted_by, group in ordered[:limit]:
        resolution_hours = group["resolution_hours"]
        entries.append(
            SubmitterLeaderboardEntry(
                submitted_by=submitted_by,
                total=group["total"],
                open=group["open"],
                resolved=group["resolved"],
                pending=group["pending"],
                last_dispute_at=group["last_dispute_at"],
                median_resolution_hours=(
                    round(float(median(resolution_hours)), 2)
                    if resolution_hours
                    else None
                ),
            )
        )

    return SubmitterLeaderboardResponse(
        total_submitters=len(groups),
        submitters=entries,
    )


def create_app() -> FastAPI:
    application = FastAPI(title="score_dispute_submitter_leaderboard_api")
    application.include_router(router, prefix="/api")
    return application


app = create_app()


def run() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SessionLocal
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id,
                    server_id,
                    submitted_by,
                    proposed_overall_risk,
                    proposed_axes,
                    explanation,
                    reason_category,
                    status,
                    admin_note,
                    created_at,
                    resolved_at
                ) VALUES (
                    :id,
                    :server_id,
                    :submitted_by,
                    :proposed_overall_risk,
                    :proposed_axes,
                    :explanation,
                    :reason_category,
                    :status,
                    :admin_note,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": 1,
                    "submitted_by": "alice",
                    "proposed_overall_risk": 0.8,
                    "proposed_axes": '{"availability": 0.8}',
                    "explanation": "Open review",
                    "reason_category": "incorrect_score",
                    "status": "open",
                    "admin_note": None,
                    "created_at": datetime(2025, 1, 1, 10, 0, 0),
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": 1,
                    "submitted_by": "alice",
                    "proposed_overall_risk": 0.7,
                    "proposed_axes": '{"availability": 0.7}',
                    "explanation": "Resolved review",
                    "reason_category": "incorrect_score",
                    "status": "resolved",
                    "admin_note": "Adjusted",
                    "created_at": datetime(2025, 1, 1, 9, 0, 0),
                    "resolved_at": datetime(2025, 1, 1, 11, 0, 0),
                },
                {
                    "id": 3,
                    "server_id": 1,
                    "submitted_by": "alice",
                    "proposed_overall_risk": 0.6,
                    "proposed_axes": '{"availability": 0.6}',
                    "explanation": "Pending review",
                    "reason_category": "incorrect_score",
                    "status": "pending",
                    "admin_note": None,
                    "created_at": datetime(2025, 1, 1, 8, 0, 0),
                    "resolved_at": None,
                },
                {
                    "id": 4,
                    "server_id": 2,
                    "submitted_by": "bob",
                    "proposed_overall_risk": 0.5,
                    "proposed_axes": '{"availability": 0.5}',
                    "explanation": "Resolved review",
                    "reason_category": "incorrect_score",
                    "status": "resolved",
                    "admin_note": "No change",
                    "created_at": datetime(2025, 1, 1, 7, 0, 0),
                    "resolved_at": datetime(2025, 1, 1, 7, 30, 0),
                },
            ],
        )

    def override_get_session():
        with SessionLocal(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/submitter-leaderboard")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["submitters"]) == 2
        assert payload["submitters"][0]["submitted_by"] == "alice"
        assert payload["submitters"][0]["total"] == 3
        assert all(
            entry["total"] >= entry["open"]
            for entry in payload["submitters"]
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()