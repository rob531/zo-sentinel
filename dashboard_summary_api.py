from fastapi import APIRouter, Depends
from typing import List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import tenant_org_model
from app.services import write_service
from app.consumers import app_scoring_consumer

router = APIRouter()

class ServerSummary(BaseModel):
    server_id: str
    verdict: str
    scored_at: datetime

class DashboardSummary(BaseModel):
    tier_distribution: Dict[str, int]
    scored_count: int
    total_count: int
    recent_scored: List[ServerSummary]

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    db: Session = Depends(get_db),
    org_scope: str = Depends(tenant_org_model.org_scope)
):
    # Get all servers in the org scope
    servers_query = f"""
    SELECT id FROM mcp_server_registry
    WHERE org_id IN ({org_scope})
    """
    servers = await write_service.query(db, servers_query)

    # Get scored servers with their verdicts and timestamps
    scored_servers_query = f"""
    SELECT
        s.id as server_id,
        a.verdict,
        a.scored_at
    FROM mcp_llm_axis_scores a
    JOIN mcp_server_registry s ON a.server_id = s.id
    WHERE s.org_id IN ({org_scope})
    ORDER BY a.scored_at DESC
    LIMIT 10
    """
    scored_servers = await write_service.query(db, scored_servers_query)

    # Get tier distribution
    tier_query = f"""
    SELECT verdict, COUNT(*) as count
    FROM mcp_llm_axis_scores a
    JOIN mcp_server_registry s ON a.server_id = s.id
    WHERE s.org_id IN ({org_scope})
    GROUP BY verdict
    """
    tiers = await write_service.query(db, tier_query)
    tier_distribution = {row['verdict']: row['count'] for row in tiers}

    # Prepare response
    response = DashboardSummary(
        tier_distribution=tier_distribution,
        scored_count=len(scored_servers),
        total_count=len(servers),
        recent_scored=[
            ServerSummary(
                server_id=row['server_id'],
                verdict=row['verdict'],
                scored_at=row['scored_at']
            ) for row in scored_servers
        ]
    )

    return response

if __name__ == "__main__":
    # Sample test data
    sample_tiers = [
        {"verdict": "tier1", "count": 5},
        {"verdict": "tier2", "count": 3},
        {"verdict": "tier3", "count": 2}
    ]
    sample_servers = [
        {"server_id": "s1", "verdict": "tier1", "scored_at": datetime(2023, 1, 1)},
        {"server_id": "s2", "verdict": "tier2", "scored_at": datetime(2023, 1, 2)},
        {"server_id": "s3", "verdict": "tier3", "scored_at": datetime(2023, 1, 3)}
    ]

    # Create sample response
    sample_response = DashboardSummary(
        tier_distribution={row['verdict']: row['count'] for row in sample_tiers},
        scored_count=len(sample_servers),
        total_count=10,
        recent_scored=[
            ServerSummary(
                server_id=row['server_id'],
                verdict=row['verdict'],
                scored_at=row['scored_at']
            ) for row in sample_servers
        ]
    )

    # Test tier distribution sum
    assert sum(sample_response.tier_distribution.values()) == sample_response.scored_count

    # Test recent scored is sorted
    recent_scored = sample_response.recent_scored
    assert all(
        recent_scored[i].scored_at >= recent_scored[i+1].scored_at
        for i in range(len(recent_scored)-1)
    )

    print("PASS")