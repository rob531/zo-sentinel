# services/staged/score_run_statistics_api/contract.py
from datetime import datetime, date
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged)
from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class RunStatisticsItem(BaseModel):
    date: date = Field(..., description="UTC date of the run")
    total_scores: int = Field(..., description="Number of rows scored on that date")
    unique_servers: int = Field(..., description="Number of distinct servers scored")
    axis_count: int = Field(..., description="Number of distinct axis names")
    avg_p_top: float = Field(..., description="Average p_top value")


class RunStatisticsResponse(BaseModel):
    statistics: List[RunStatisticsItem]


@router.get(
    "/scoring/run-statistics",
    response_model=RunStatisticsResponse,
    summary="Daily run statistics for LLM axis scores",
)
def get_run_statistics(session: Session = Depends(get_session)):
    """
    Returns per‑day aggregated statistics for `McpLlmAxisScore`.
    """
    # PostgreSQL uses date_trunc, SQLite uses date()
    if session.bind.dialect.name == "postgresql":
        day_expr = func.date_trunc("day", McpLlmAxisScore.scored_at).label("day")
    else:  # SQLite, etc.
        day_expr = func.date(McpLlmAxisScore.scored_at).label("day")

    rows = (
        session.query(
            day_expr,
            func.count().label("total_scores"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("unique_servers"),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .group_by(day_expr)
        .order_by(day_expr)
        .all()
    )

    stats = [
        RunStatisticsItem(
            date=row.day,
            total_scores=row.total_scores,
            unique_servers=row.unique_servers,
            axis_count=row.axis_count,
            avg_p_top=round(row.avg_p_top, 6) if row.avg_p_top is not None else 0.0,
        )
        for row in rows
    ]

    return RunStatisticsResponse(statistics=stats)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.score_run_statistics_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.include_router(router)

    # SQLite in‑memory engine (StaticPool keeps a single connection alive)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        with TestSessionLocal() as sess:
            yield sess

    test_app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data (5 rows across 3 distinct dates)
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as sess:
        seed = [
            McpLlmAxisScore(
                id=1,
                adapter_sha256="a1",
                axis_name="axis1",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label="lbl",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.9,
                probs="{}",
                scored_at=datetime(2023, 1, 1, 10, 0, 0),
                server_id=1,
            ),
            McpLlmAxisScore(
                id=2,
                adapter_sha256="a2",
                axis_name="axis2",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label="lbl",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.8,
                probs="{}",
                scored_at=datetime(2023, 1, 1, 12, 0, 0),
                server_id=1,
            ),
            McpLlmAxisScore(
                id=3,
                adapter_sha256="a3",
                axis_name="axis1",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label="lbl",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.7,
                probs="{}",
                scored_at=datetime(2023, 1, 2, 9, 0, 0),
                server_id=2,
            ),
            McpLlmAxisScore(
                id=4,
                adapter_sha256="a4",
                axis_name="axis1",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label="lbl",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.6,
                probs="{}",
                scored_at=datetime(2023, 1, 3, 15, 0, 0),
                server_id=3,
            ),
            McpLlmAxisScore(
                id=5,
                adapter_sha256="a5",
                axis_name="axis2",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label="lbl",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.5,
                probs="{}",
                scored_at=datetime(2023, 1, 3, 16, 0, 0),
                server_id=3,
            ),
        ]
        sess.add_all(seed)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    resp = client.get("/api/scoring/run-statistics")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    stats = data.get("statistics", [])
    if len(stats) != 3:
        print(f"FAIL: expected 3 statistic entries, got {len(stats)}", file=sys.stderr)
        sys.exit(1)

    # Expected aggregates
    expected = {
        "2023-01-01": {"total_scores": 2, "unique_servers": 1, "axis_count": 2, "avg_p_top": 0.85},
        "2023-01-02": {"total_scores": 1, "unique_servers": 1, "axis_count": 1, "avg_p_top": 0.7},
        "2023-01-03": {"total_scores": 2, "unique_servers": 1, "axis_count": 2, "avg_p_top": 0.55},
    }

    for entry in stats:
        d = entry["date"]
        if d not in expected:
            print(f"FAIL: unexpected date {d}", file=sys.stderr)
            sys.exit(1)
        exp = expected[d]
        if (
            entry["total_scores"] != exp["total_scores"]
            or entry["unique_servers"] != exp["unique_servers"]
            or entry["axis_count"] != exp["axis_count"]
            or round(entry["avg_p_top"], 2) != round(exp["avg_p_top"], 2)
        ):
            print(f"FAIL: mismatch on {d}: got {entry}, expected {exp}", file=sys.stderr)
            sys.exit(1)

    print("PASS")
    sys.exit(0)