# deps: fastapi, sqlalchemy, pydantic
"""Score Run Statistics API -- per-day aggregation of LLM axis score runs.

GET /api/scoring/run-statistics
  Returns daily run statistics: total_scores, unique_servers, axis_count, avg_p_top.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["score_run_statistics_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class RunStatisticsItem(BaseModel):
    date: str = Field(..., description="Date of the run (YYYY-MM-DD)")
    total_scores: int = Field(..., description="Number of axis-score rows scored that day")
    unique_servers: int = Field(..., description="Number of distinct servers scored")
    axis_count: int = Field(..., description="Number of distinct axis names")
    avg_p_top: float = Field(..., description="Average p_top value for the day")


class RunStatisticsResponse(BaseModel):
    statistics: List[RunStatisticsItem]


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/scoring/run-statistics", response_model=RunStatisticsResponse)
def get_score_run_statistics(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_session),
) -> RunStatisticsResponse:
    """
    Return per-day aggregated statistics for McpLlmAxisScore rows.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Use func.date() which works for both PostgreSQL and SQLite
    from sqlalchemy import func

    rows = (
        db.query(
            func.date(McpLlmAxisScore.scored_at).label("day"),
            func.count().label("total_scores"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("unique_servers"),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .filter(func.date(McpLlmAxisScore.scored_at) >= cutoff.date())
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at))
        .all()
    )

    statistics = [
        RunStatisticsItem(
            date=str(row.day) if row.day else "",
            total_scores=row.total_scores,
            unique_servers=row.unique_servers,
            axis_count=row.axis_count,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else 0.0,
        )
        for row in rows
    ]

    return RunStatisticsResponse(statistics=statistics)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    now = datetime.now(timezone.utc)

    # Seed: 5 rows across 3 distinct dates
    seed_rows = [
        # Day 1: 2 rows, 1 server, 2 axes
        McpLlmAxisScore(
            id=1, server_id="srv-1", axis_name="overall_risk",
            model_version="m1", label="HIGH", label_index=2,
            p_top=0.9, p_critical=0.1, p_danger=0.2,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="a" * 64,
            scored_at=datetime(2025, 1, 1, 10, 0, 0),
        ),
        McpLlmAxisScore(
            id=2, server_id="srv-1", axis_name="auth_strength",
            model_version="m1", label="MEDIUM", label_index=1,
            p_top=0.8, p_critical=0.05, p_danger=0.1,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="a" * 64,
            scored_at=datetime(2025, 1, 1, 12, 0, 0),
        ),
        # Day 2: 1 row, 1 server, 1 axis
        McpLlmAxisScore(
            id=3, server_id="srv-2", axis_name="overall_risk",
            model_version="m1", label="LOW", label_index=0,
            p_top=0.7, p_critical=0.01, p_danger=0.05,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="b" * 64,
            scored_at=datetime(2025, 1, 2, 9, 0, 0),
        ),
        # Day 3: 2 rows, 2 servers, 2 axes
        McpLlmAxisScore(
            id=4, server_id="srv-3", axis_name="overall_risk",
            model_version="m1", label="CRITICAL", label_index=3,
            p_top=0.6, p_critical=0.5, p_danger=0.6,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="c" * 64,
            scored_at=datetime(2025, 1, 3, 15, 0, 0),
        ),
        McpLlmAxisScore(
            id=5, server_id="srv-4", axis_name="auth_strength",
            model_version="m1", label="HIGH", label_index=2,
            p_top=0.5, p_critical=0.05, p_danger=0.1,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="d" * 64,
            scored_at=datetime(2025, 1, 3, 16, 0, 0),
        ),
    ]

    with SessionLocal() as db:
        for row in seed_rows:
            db.add(row)
        db.commit()

    def _override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # Happy path
    resp = client.get("/api/scoring/run-statistics")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "statistics" in data, "Missing 'statistics' key"
    stats = data["statistics"]
    assert isinstance(stats, list), "'statistics' must be a list"
    assert len(stats) == 3, f"Expected 3 date entries, got {len(stats)}: {stats}"

    expected = {
        "2025-01-01": {"total_scores": 2, "unique_servers": 1, "axis_count": 2, "avg_p_top": 0.85},
        "2025-01-02": {"total_scores": 1, "unique_servers": 1, "axis_count": 1, "avg_p_top": 0.7},
        "2025-01-03": {"total_scores": 2, "unique_servers": 2, "axis_count": 2, "avg_p_top": 0.55},
    }

    for entry in stats:
        d = entry["date"]
        assert d in expected, f"Unexpected date {d}"
        exp = expected[d]
        assert entry["total_scores"] == exp["total_scores"], (
            f"total_scores mismatch on {d}: got {entry['total_scores']}, want {exp['total_scores']}"
        )
        assert entry["unique_servers"] == exp["unique_servers"], (
            f"unique_servers mismatch on {d}: got {entry['unique_servers']}, want {exp['unique_servers']}"
        )
        assert entry["axis_count"] == exp["axis_count"], (
            f"axis_count mismatch on {d}: got {entry['axis_count']}, want {exp['axis_count']}"
        )
        assert round(entry["avg_p_top"], 2) == round(exp["avg_p_top"], 2), (
            f"avg_p_top mismatch on {d}: got {entry['avg_p_top']}, want {exp['avg_p_top']}"
        )

    # Auth: public endpoint - no 401/403 expected
    print("PASS")
    sys.exit(0)
