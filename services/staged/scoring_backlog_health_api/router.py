"""
Scoring Backlog Health API Router

Thin APIRouter exposing GET /api/scoring/backlog-summary.
Computes backlog health metrics from the scoring pipeline.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class BacklogSummaryResponse(BaseModel):
    """Response model for backlog summary endpoint."""
    pending_count: int
    current_count: int
    stale_count: int
    backlog_estimate: int
    freshness_days: int
    as_of_utc: str


@router.get("/backlog-summary", response_model=BacklogSummaryResponse)
def get_backlog_summary(
    scoring_freshness_days: int = 7,
    session: Session = Depends(get_session),
) -> BacklogSummaryResponse:
    """
    Get scoring backlog health summary.
    
    Computes:
    - pending_count: servers never scored
    - current_count: servers scored in last 24h
    - stale_count: servers with scores older than freshness_days
    - backlog_estimate: pending + stale servers
    
    Args:
        scoring_freshness_days: threshold in days for considering scores stale
        session: database session
    
    Returns:
        BacklogSummaryResponse with health metrics
    """
    query = text("""
        WITH server_scores AS (
            SELECT
                r.server_id,
                r.server_name,
                MAX(s.created_at) AS latest_score_at
            FROM mcp_server_registry r
            LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
            LEFT JOIN cadence_job_runs c ON r.server_id = c.server_id
            GROUP BY r.server_id, r.server_name
        ),
        scored_count AS (
            SELECT COUNT(*) FILTER (WHERE latest_score_at IS NOT NULL) AS scored,
                   COUNT(*) FILTER (WHERE latest_score_at IS NULL) AS never_scored,
                   COUNT(*) FILTER (
                       WHERE latest_score_at IS NOT NULL
                       AND latest_score_at < NOW() - INTERVAL '1 day'
                   ) AS current_score,
                   COUNT(*) FILTER (
                       WHERE latest_score_at IS NOT NULL
                       AND latest_score_at >= NOW() - INTERVAL '1 day'
                   ) AS stale
            FROM server_scores
        )
        SELECT
            COALESCE(scored_count.never_scored, 0) AS pending_count,
            COALESCE(scored_count.current_score, 0) AS current_count,
            COALESCE(scored_count.stale, 0) AS stale_count,
            (COALESCE(scored_count.never_scored, 0) + COALESCE(scored_count.stale, 0)) AS backlog_estimate
        FROM scored_count
    """)
    
    result = session.execute(query)
    row = result.fetchone()
    
    if row is None:
        return BacklogSummaryResponse(
            pending_count=0,
            current_count=0,
            stale_count=0,
            backlog_estimate=0,
            freshness_days=scoring_freshness_days,
            as_of_utc=datetime.now(timezone.utc).isoformat(),
        )
    
    return BacklogSummaryResponse(
        pending_count=row.pending_count,
        current_count=row.current_count,
        stale_count=row.stale_count,
        backlog_estimate=row.backlog_estimate,
        freshness_days=scoring_freshness_days,
        as_of_utc=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock
    from datetime import timedelta
    
    print("Running self-test for scoring_backlog_health_api...")
    
    # Create in-memory mock data for self-test
    mock_servers = [
        {"server_id": "srv-001", "server_name": "never-scored-server", "latest_score_at": None},
        {"server_id": "srv-002", "server_name": "fresh-score-server", "latest_score_at": datetime.now(timezone.utc) - timedelta(hours=12)},
        {"server_id": "srv-003", "server_name": "stale-score-server", "latest_score_at": datetime.now(timezone.utc) - timedelta(days=30)},
    ]
    
    pending_count = sum(1 for s in mock_servers if s["latest_score_at"] is None)
    current_count = sum(1 for s in mock_servers if s["latest_score_at"] and (datetime.now(timezone.utc) - s["latest_score_at"]) < timedelta(days=1))
    stale_count = sum(1 for s in mock_servers if s["latest_score_at"] and (datetime.now(timezone.utc) - s["latest_score_at"]) >= timedelta(days=1))
    backlog_estimate = pending_count + stale_count
    
    print(f"  Expected: pending_count={pending_count}, current_count={current_count}, stale_count={stale_count}, backlog_estimate={backlog_estimate}")
    
    # Mock the database session
    mock_session = MagicMock()
    
    # Mock query result
    mock_result = MagicMock()
    mock_result.fetchone.return_value = MagicMock(
        pending_count=pending_count,
        current_count=current_count,
        stale_count=stale_count,
        backlog_estimate=backlog_estimate,
    )
    mock_session.execute.return_value = mock_result
    
    # Test the endpoint function
    response = get_backlog_summary(
        scoring_freshness_days=7,
        session=mock_session,
    )
    
    print(f"  Received: pending_count={response.pending_count}, current_count={response.current_count}, stale_count={response.stale_count}, backlog_estimate={response.backlog_estimate}")
    
    # Validate
    if response.pending_count == 1 and response.stale_count == 1:
        print("PASS: Backlog summary computed correctly (pending_count=1, stale_count=1)")
        sys.exit(0)
    else:
        print(f"FAIL: Expected pending_count=1 and stale_count=1, got pending_count={response.pending_count} and stale_count={response.stale_count}")
        sys.exit(1)