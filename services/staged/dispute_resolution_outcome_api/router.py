from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_resolution_outcome as _get_dispute_resolution_outcome

router = APIRouter(prefix="/api")


class DisputeStatusCount(BaseModel):
    status: str
    count: int


class AdminNoteCoverage(BaseModel):
    with_note: int
    without_note: int
    coverage_rate: float


class DisputeResolutionOutcomeResponse(BaseModel):
    total: int
    by_status: list[DisputeStatusCount]
    admin_note_coverage: AdminNoteCoverage
    median_resolution_days: float | None


@router.get(
    "/disputes/resolution-outcome",
    response_model=DisputeResolutionOutcomeResponse,
)
def get_dispute_resolution_outcome(
    session: Session = Depends(get_session),
) -> DisputeResolutionOutcomeResponse:
    return _get_dispute_resolution_outcome(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta
    from statistics import median

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    status TEXT,
                    admin_note TEXT,
                    created_at DATETIME,
                    resolved_at DATETIME
                )
                """
            )
        )

        base = datetime(2025, 1, 1, 12, 0, 0)
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, server_id, status, admin_note, created_at, resolved_at)
                VALUES
                    (1, 1, 'resolved', 'Approved', :base, :base_plus_2),
                    (2, 2, 'resolved', 'Rejected', :base_plus_1, :base_plus_5),
                    (3, 3, 'open', NULL, :base_plus_2, NULL),
                    (4, 4, 'rejected', NULL, :base_plus_3, NULL),
                    (5, 5, 'resolved', NULL, :base, :base_plus_4)
                """
            ),
            {
                "base": base,
                "base_plus_1": base + timedelta(days=1),
                "base_plus_2": base + timedelta(days=2),
                "base_plus_3": base + timedelta(days=3),
                "base_plus_4": base + timedelta(days=4),
                "base_plus_5": base + timedelta(days=5),
            },
        )

    def test_get_session():
        with test_engine.connect() as connection:
            yield connection

    def test_outcome(session):
        rows = session.execute(
            text(
                """
                SELECT status, admin_note, created_at, resolved_at
                FROM mcp_score_disputes
                WHERE status IS NOT NULL
                """
            )
        ).mappings().all()

        status_counts: dict[str, int] = {}
        with_note = 0
        resolution_days: list[float] = []

        for row in rows:
            status = row["status"]
            status_counts[status] = status_counts.get(status, 0) + 1

            if row["admin_note"] is not None:
                with_note += 1

            if status == "resolved" and row["resolved_at"] is not None:
                created_at = row["created_at"]
                resolved_at = row["resolved_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)
                if isinstance(resolved_at, str):
                    resolved_at = datetime.fromisoformat(resolved_at)
                resolution_days.append(
                    (resolved_at - created_at).total_seconds() / 86400
                )

        total = len(rows)
        without_note = total - with_note

        return {
            "total": total,
            "by_status": [
                {"status": status, "count": count}
                for status, count in sorted(status_counts.items())
            ],
            "admin_note_coverage": {
                "with_note": with_note,
                "without_note": without_note,
                "coverage_rate": with_note / total if total else 0.0,
            },
            "median_resolution_days": (
                float(median(resolution_days)) if resolution_days else None
            ),
        }

    original_outcome = _get_dispute_resolution_outcome
    _get_dispute_resolution_outcome = test_outcome

    try:
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = test_get_session

        response = TestClient(test_app).get(
            "/api/disputes/resolution-outcome"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert len(payload["by_status"]) == 3
        assert payload["median_resolution_days"] >= 0
        assert payload["admin_note_coverage"]["without_note"] >= 1
    finally:
        _get_dispute_resolution_outcome = original_outcome

    print("PASS")