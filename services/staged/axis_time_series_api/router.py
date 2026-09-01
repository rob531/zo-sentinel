from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


class TimeSeriesPoint(BaseModel):
    scored_at: str
    p_top: float | None = None
    p_critical: float | None = None
    label: str | None = None


class TimeSeriesResponse(BaseModel):
    server_id: str
    axis: str
    days_requested: int
    points: List[TimeSeriesPoint]


def get_axis_time_series(
    server_id: str,
    axis: str,
    days: int,
    session: Session
) -> TimeSeriesResponse:
    from sqlalchemy import and_, select

    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(McpLlmAxisScore)
        .where(
            and_(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == axis,
                McpLlmAxisScore.scored_at >= cutoff
            )
        )
        .order_by(McpLlmAxisScore.scored_at)
    )
    rows = session.execute(stmt).scalars().all()

    points = [
        TimeSeriesPoint(
            scored_at=r.scored_at.isoformat(),
            p_top=r.p_top,
            p_critical=r.p_critical,
            label=r.label
        )
        for r in rows
    ]
    return TimeSeriesResponse(
        server_id=server_id,
        axis=axis,
        days_requested=days,
        points=points
    )


@router.get("/api/axis/timeseries", response_model=TimeSeriesResponse)
def get_timeseries(
    server_id: str = Query(..., description="Server ID"),
    axis: str = Query(..., description="Axis name"),
    days: int = Query(..., ge=1, description="Number of days"),
    session: Session = Depends(get_session)
) -> TimeSeriesResponse:
    return get_axis_time_series(server_id, axis, days, session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                scored_at TIMESTAMP NOT NULL,
                escalated INTEGER,
                escalated_to TEXT
            )
        """))

    TestingSessionLocal = sessionmaker(bind=engine)
    app = FastAPI()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/axis/timeseries", response_model=TimeSeriesResponse)
    def test_endpoint(
        server_id: str = Query(...),
        axis: str = Query(...),
        days: int = Query(...)
    ):
        session = next(override_get_session())
        return get_axis_time_series(server_id, axis, days, session)

    session = TestingSessionLocal()
    now = datetime.utcnow()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = base
    day2 = base + timedelta(days=1)
    day3 = base + timedelta(days=2)

    seed_data = [
        McpLlmAxisScore(
            id=1, server_id="server-alpha", axis_name="security",
            p_top=0.2, p_critical=0.05, label="low", scored_at=day1
        ),
        McpLlmAxisScore(
            id=2, server_id="server-alpha", axis_name="security",
            p_top=0.4, p_critical=0.1, label="medium", scored_at=day1 + timedelta(hours=8)
        ),
        McpLlmAxisScore(
            id=3, server_id="server-alpha", axis_name="security",
            p_top=0.6, p_critical=0.2, label="high", scored_at=day2 + timedelta(hours=4)
        ),
        McpLlmAxisScore(
            id=4, server_id="server-alpha", axis_name="security",
            p_top=0.7, p_critical=0.3, label="high", scored_at=day2 + timedelta(hours=12)
        ),
        McpLlmAxisScore(
            id=5, server_id="server-alpha", axis_name="security",
            p_top=0.9, p_critical=0.5, label="critical", scored_at=day3 + timedelta(hours=6)
        ),
    ]
    session.add_all(seed_data)
    session.commit()

    response = test_endpoint(server_id="server-alpha", axis="security", days=3)
    session.close()

    assert len(response.points) == 5, f"Expected 5 points, got {len(response.points)}"

    from dateutil.parser import isoparse
    for pt in response.points:
        isoparse(pt.scored_at)
        assert pt.p_top is not None, "p_top should not be None"
        assert 0 <= pt.p_top <= 1, f"p_top {pt.p_top} out of range [0,1]"

    print("PASS")