"""services/staged/scoring_wave_progress_api/contract.py

FastAPI contract for the *scoring_wave_progress_api* service.

The module defines a single GET endpoint:
    GET /api/scoring/wave-progress

It returns a JSON payload describing the progress of each scoring wave
as stored in the authoritative Postgres tables
`cadence_job_runs` and `mcp_llm_axis_scores`.

Running the module directly executes a self‑test that builds an
in‑memory SQLite database, seeds it with mock data, calls the endpoint
via a TestClient and asserts the expected shape and values.
If all assertions pass, the script prints ``PASS`` and exits with status 0.
"""

from __future__ import annotations

import datetime
import statistics
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from app.db import Base, get_session
from app.models import CadenceJobRun, McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class WaveProgress(BaseModel):
    wave_id: int = Field(..., description="Primary key of the CadenceJobRun row")
    job_id: str = Field(..., description="Job identifier string")
    status: str = Field(..., description="Current status of the wave")
    servers_scored: int = Field(..., description="Number of distinct servers that have been scored")
    total_servers: int = Field(..., description="Total number of servers expected for the wave")
    pct_complete: float = Field(..., description="Percentage of servers scored")
    median_latency_seconds: Optional[float] = Field(
        None,
        description="Median latency (seconds) between wave start and each score timestamp",
    )
    started_at: datetime.datetime = Field(..., description="Wave start timestamp")
    finished_at: Optional[datetime.datetime] = Field(
        None, description="Wave finish timestamp (may be null while running)"
    )


class WaveProgressResponse(BaseModel):
    waves: List[WaveProgress] = Field(..., description="List of wave progress objects")


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/scoring/wave-progress",
    response_model=WaveProgressResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve scoring progress for each wave",
)
def get_wave_progress(db: Session = Depends(get_session)) -> WaveProgressResponse:
    """Collect progress information for each scoring wave.

    The function reads `cadence_job_runs` to discover waves and
    correlates them with `mcp_llm_axis_scores` to compute per‑wave
    statistics.
    """
    runs = db.query(CadenceJobRun).order_by(CadenceJobRun.id).all()
    if not runs:
        raise HTTPException(status_code=404, detail="No wave runs found")

    waves: List[WaveProgress] = []

    for run in runs:
        # Scores that belong to this wave are those whose timestamp falls
        # between the wave's start and finish (if finished_at is set).
        score_query = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.scored_at >= run.started_at,
        )
        if run.finished_at:
            score_query = score_query.filter(McpLlmAxisScore.scored_at <= run.finished_at)

        scores = score_query.all()

        servers_scored = len({s.server_id for s in scores})
        total_servers = run.rows_affected or 0

        pct_complete = (
            (servers_scored / total_servers) * 100.0 if total_servers else 0.0
        )

        latencies = [
            (s.scored_at - run.started_at).total_seconds() for s in scores
        ]
        median_latency = (
            statistics.median(latencies) if latencies else None
        )

        wave = WaveProgress(
            wave_id=run.id,
            job_id=run.job,
            status=run.status,
            servers_scored=servers_scored,
            total_servers=total_servers,
            pct_complete=round(pct_complete, 2),
            median_latency_seconds=median_latency,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        waves.append(wave)

    return WaveProgressResponse(waves=waves)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override that yields a session bound to the in‑memory DB
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed mock data
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    ten_min_ago = now - datetime.timedelta(minutes=10)
    five_min_ago = now - datetime.timedelta(minutes=5)

    with TestSessionLocal() as db:
        # Wave 1 – partially completed (3 of 5 servers scored)
        wave1 = CadenceJobRun(
            id=1,
            job="wave_1",
            status="running",
            detail="mock wave 1",
            started_at=ten_min_ago,
            finished_at=None,
            rows_affected=5,
        )
        # Wave 2 – fully completed (5 of 5 servers scored)
        wave2 = CadenceJobRun(
            id=2,
            job="wave_2",
            status="completed",
            detail="mock wave 2",
            started_at=ten_min_ago,
            finished_at=now,
            rows_affected=5,
        )
        db.add_all([wave1, wave2])
        db.flush()  # obtain PKs if needed

        # Scores for wave 1 (servers 1‑3)
        scores_wave1 = [
            McpLlmAxisScore(
                id=100 + i,
                server_id=i,
                axis_name="axis_a",
                label="label_a",
                label_index=0,
                model_version="v1",
                p_top=0.9,
                p_critical=0.1,
                p_danger=0.0,
                probs={},
                scored_at=ten_min_ago + datetime.timedelta(minutes=2 + i),
                adapter_sha256="sha256",
                decision_rule_version="dr1",
                escalated=False,
                escalated_to=None,
            )
            for i in range(1, 4)
        ]

        # Scores for wave 2 (servers 1‑5)
        scores_wave2 = [
            McpLlmAxisScore(
                id=200 + i,
                server_id=i,
                axis_name="axis_b",
                label="label_b",
                label_index=0,
                model_version="v1",
                p_top=0.8,
                p_critical=0.2,
                p_danger=0.0,
                probs={},
                scored_at=ten_min_ago + datetime.timedelta(minutes=3 + i),
                adapter_sha256="sha256",
                decision_rule_version="dr1",
                escalated=False,
                escalated_to=None,
            )
            for i in range(1, 6)
        ]

        db.add_all(scores_wave1 + scores_wave2)
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with the router and dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the request and perform assertions
    # ------------------------------------------------------------------- #
    response = client.get("/api/scoring/wave-progress")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert "waves" in payload, "Response missing 'waves' key"
    assert isinstance(payload["waves"], list), "'waves' is not a list"
    assert len(payload["waves"]) == 2, f"Expected 2 waves, got {len(payload['waves'])}"

    # At least one wave must report a non‑zero completion percentage
    pct_values = [w["pct_complete"] for w in payload["waves"]]
    assert any(p > 0 for p in pct_values), "All pct_complete values are zero"

    print("PASS")
    sys.exit(0)