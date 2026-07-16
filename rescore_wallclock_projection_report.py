from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from typing import List, Dict, Any
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/reports", tags=["reports"])

def get_cadence_throughput(session: Session) -> float:
    """Calculate median rows_affected_per_hour from successful cadence_job_runs in last 24h."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": """
                SELECT
                    AVG(rows_affected) as avg_rows,
                    COUNT(*) as run_count
                FROM cadence_job_runs
                WHERE
                    (job LIKE '%score%' OR job LIKE '%verdict%' OR job LIKE '%axis%')
                    AND status = 'success'
                    AND finished_at >= NOW() - INTERVAL '24 hours'
            """
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch cadence throughput")
    data = response.json()
    if not data or data[0]['run_count'] == 0:
        return 0.0
    return data[0]['avg_rows'] / 24  # avg per hour

def get_never_scored_servers(session: Session) -> int:
    """Count servers in registry with no scores in mcp_llm_axis_scores."""
    subquery = session.query(MCPAxisScores.server_id).subquery()
    return session.query(MCPServerRegistry).filter(
        ~MCPServerRegistry.server_id.in_(subquery)
    ).count()

def get_recently_scored_servers(session: Session) -> int:
    """Count servers scored in the last 24h."""
    return session.query(MCPAxisScores).filter(
        MCPAxisScores.scored_at >= datetime.utcnow() - timedelta(hours=24)
    ).count()

@router.get("/rescore-wallclock-projection", response_model=Dict[str, Any])
async def rescore_wallclock_projection(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Project coverage of never-scored servers over time horizons."""
    total_backlog = get_never_scored_servers(session)
    throughput = get_cadence_throughput(session)

    if throughput == 0:
        return {
            "horizons": [],
            "as_of": datetime.utcnow().isoformat(),
            "cadence_lookback_hours": 24
        }

    horizons = [
        {"hours": h, "projected_covered": min(int(h * throughput), total_backlog)}
        for h in [1, 4, 24]
    ]

    for h in horizons:
        h["total_backlog"] = total_backlog
        h["coverage_pct"] = (h["projected_covered"] / total_backlog) * 100 if total_backlog > 0 else 0.0

    return {
        "horizons": horizons,
        "as_of": datetime.utcnow().isoformat(),
        "cadence_lookback_hours": 24
    }

if __name__ == "__main__":
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Add some servers to registry
        for i in range(100):
            session.add(MCPServerRegistry(server_id=f"server_{i}"))
        # Add scores for some servers
        for i in range(20):
            session.add(MCPAxisScores(server_id=f"server_{i}", scored_at=datetime.utcnow()))
        session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/reports/rescore-wallclock-projection")
    assert response.status_code == 200
    data = response.json()

    # Assertions
    assert "horizons" in data
    assert data["horizons"][0]["total_backlog"] == 80  # 100 total - 20 scored
    assert data["horizons"][0]["coverage_pct"] == (data["horizons"][0]["projected_covered"] / 80) * 100

    print("PASS")