from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["scoring_velocity"])


class ScoringVelocityResponse(BaseModel):
    rows_scored_1h: int
    rows_scored_24h: int
    active_jobs: int
    recent_wave_ids: list[int]
    backlog_estimate: int
    last_run_elapsed_s: Optional[float]
    as_of: str


def get_scoring_velocity(session: Session = Depends(get_session)) -> ScoringVelocityResponse:
    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    twenty_four_hours_ago = now - timedelta(hours=24)
    as_of = now.isoformat()

    # rows_scored_1h: count axis scores in last hour
    result_1h = session.execute(
        text("SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE scored_at >= :since"),
        {"since": one_hour_ago}
    )
    rows_scored_1h = result_1h.scalar() or 0

    # rows_scored_24h: count axis scores in last 24 hours
    result_24h = session.execute(
        text("SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE scored_at >= :since"),
        {"since": twenty_four_hours_ago}
    )
    rows_scored_24h = result_24h.scalar() or 0

    # active_jobs: count running jobs
    result_active = session.execute(
        text("SELECT COUNT(*) FROM cadence_job_runs WHERE status = 'running'")
    )
    active_jobs = result_active.scalar() or 0

    # recent_wave_ids: last 5 wave_ids extracted from detail column of recent runs
    result_waves = session.execute(
        text("""
            SELECT detail 
            FROM cadence_job_runs 
            WHERE detail IS NOT NULL 
            ORDER BY started_at DESC 
            LIMIT 100
        """)
    )
    wave_ids = []
    for row in result_waves.fetchall():
        detail = row[0]
        if detail:
            import json
            try:
                if isinstance(detail, str):
                    parsed = json.loads(detail)
                else:
                    parsed = detail
                if "wave_id" in parsed:
                    wave_ids.append(parsed["wave_id"])
            except Exception:
                pass
        if len(wave_ids) >= 5:
            break
    recent_wave_ids = wave_ids[:5]

    # backlog_estimate: servers in registry with no axis score in last 24h
    result_backlog = session.execute(
        text("""
            SELECT COUNT(DISTINCT r.id) 
            FROM mcp_server_registry r
            WHERE NOT EXISTS (
                SELECT 1 FROM mcp_llm_axis_scores s 
                WHERE s.server_id = r.id AND s.scored_at >= :since
            )
        """),
        {"since": twenty_four_hours_ago}
    )
    backlog_estimate = result_backlog.scalar() or 0

    # last_run_elapsed_s: elapsed seconds for most recent finished scoring run
    result_last = session.execute(
        text("""
            SELECT started_at, finished_at 
            FROM cadence_job_runs 
            WHERE job LIKE '%scoring%' AND finished_at IS NOT NULL
            ORDER BY finished_at DESC 
            LIMIT 1
        """)
    )
    last_run_elapsed_s = None
    last_row = result_last.fetchone()
    if last_row:
        started_at, finished_at = last_row
        if started_at and finished_at:
            delta = finished_at - started_at
            last_run_elapsed_s = delta.total_seconds()

    return ScoringVelocityResponse(
        rows_scored_1h=rows_scored_1h,
        rows_scored_24h=rows_scored_24h,
        active_jobs=active_jobs,
        recent_wave_ids=recent_wave_ids,
        backlog_estimate=backlog_estimate,
        last_run_elapsed_s=last_run_elapsed_s,
        as_of=as_of
    )


@router.get("/scoring/velocity", response_model=ScoringVelocityResponse)
def get_velocity(session: Session = Depends(get_session)) -> ScoringVelocityResponse:
    return get_scoring_velocity(session)


if __name__ == "__main__":
    import json
    from unittest.mock import MagicMock, patch
    from datetime import datetime as dt

    from fastapi.testclient import TestClient

    # Mock session that returns test data
    mock_session = MagicMock()

    # Setup mock results for different queries
    def mock_execute(sql, params=None):
        mock_result = MagicMock()

        # rows_scored_1h
        if "scored_at >=" in str(sql) and "24" not in str(sql):
            mock_result.scalar.return_value = 42
        # rows_scored_24h
        elif "scored_at >=" in str(sql) and "24" in str(sql):
            mock_result.scalar.return_value = 150
        # active_jobs
        elif "status = 'running'" in str(sql):
            mock_result.scalar.return_value = 3
        # backlog
        elif "NOT EXISTS" in str(sql):
            mock_result.scalar.return_value = 5
        # last run
        elif "finished_at IS NOT NULL" in str(sql):
            mock_result.fetchone.return_value = (
                dt(2024, 1, 1, 12, 0, 0),
                dt(2024, 1, 1, 12, 0, 30)
            )
        # wave_ids
        elif "detail IS NOT NULL" in str(sql):
            mock_result.fetchall.return_value = [
                (json.dumps({"wave_id": 1001}),),
                (json.dumps({"wave_id": 1002}),),
                (json.dumps({"wave_id": 1003}),),
                (json.dumps({"wave_id": 1004}),),
                (json.dumps({"wave_id": 1005}),),
            ]
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None

        return mock_result

    mock_session.execute = mock_execute

    # Create a mock get_session
    def mock_get_session():
        yield mock_session

    # Import router after mocking
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Override the dependency
    app.dependency_overrides[get_session] = mock_get_session

    response = client.get("/api/scoring/velocity")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["backlog_estimate"] >= 0, f"backlog_estimate must be >= 0, got {data['backlog_estimate']}"

    print(f"Response: {data}")
    print("PASS")