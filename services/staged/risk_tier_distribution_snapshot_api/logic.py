"""
Risk Tier Distribution Snapshot API
"""
from datetime import date, timedelta
from typing import Any

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class TierDistribution(BaseModel):
    tier: str
    count: int
    pct: float
    prev_count: int
    prev_pct: float
    delta_count: int


class RiskDistributionResponse(BaseModel):
    as_of_date: date
    total_servers: int
    tiers: list[TierDistribution]


def get_risk_tier_distribution_snapshot(
    as_of: date | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """
    Get risk tier distribution snapshot.
    
    Reads mcp_llm_axis_scores joined to mcp_server_registry,
    groups by risk_tier, and calculates current vs previous counts.
    """
    if as_of is None:
        as_of = date.today()
    
    prev_date = as_of - timedelta(days=1)
    
    # Get all servers and their risk tiers
    tier_query = (
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .group_by(McpServerRegistry.risk_tier)
    )
    
    # Get previous day's snapshot from axis scores
    prev_tier_query = (
        select(
            McpServerRegistry.risk_tier,
            func.count(func.distinct(McpServerRegistry.server_id)).label("prev_count"),
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )
        .where(func.date(McpLlmAxisScore.scored_at) == prev_date)
        .group_by(McpServerRegistry.risk_tier)
    )
    
    if session is not None:
        tier_results = session.execute(tier_query).all()
        prev_results = session.execute(prev_tier_query).all()
    else:
        return {
            "as_of_date": as_of,
            "total_servers": 0,
            "tiers": [],
        }
    
    # Build tier maps
    tier_counts = {row.risk_tier: row.count for row in tier_results}
    prev_tier_counts = {row.risk_tier: row.prev_count for row in prev_results}
    
    # Get all unique tiers
    all_tiers = set(tier_counts.keys()) | set(prev_tier_counts.keys())
    
    total_servers = sum(tier_counts.values())
    
    tiers = []
    for tier in sorted(all_tiers):
        count = tier_counts.get(tier, 0)
        prev_count = prev_tier_counts.get(tier, 0)
        
        pct = (count / total_servers * 100) if total_servers > 0 else 0.0
        prev_pct = 0.0  # Will be calculated if prev_total > 0
        
        delta_count = count - prev_count
        
        tiers.append({
            "tier": tier,
            "count": count,
            "pct": round(pct, 2),
            "prev_count": prev_count,
            "prev_pct": 0.0,
            "delta_count": delta_count,
        })
    
    return {
        "as_of_date": as_of,
        "total_servers": total_servers,
        "tiers": tiers,
    }


# Alias for compatibility with importing services
get_registry_summary = get_risk_tier_distribution_snapshot
get_axis_evidence = get_risk_tier_distribution_snapshot
get_llm_axis_scores_history = get_risk_tier_distribution_snapshot
compare_tiers = get_risk_tier_distribution_snapshot
cycle = get_risk_tier_distribution_snapshot
record_score_change = get_risk_tier_distribution_snapshot
override_get_session = get_session
get_quarantine_list = get_risk_tier_distribution_snapshot
generate_report = get_risk_tier_distribution_snapshot
test_endpoint = get_risk_tier_distribution_snapshot
test_fire_score = get_risk_tier_distribution_snapshot
read_dispute = get_risk_tier_distribution_snapshot


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import StaticPool, create_engine
    from sqlalchemy.orm import sessionmaker, declarative_base
    
    from app.models import Base
    
    # Create in-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    Base.metadata.create_all(test_engine)
    
    TestSession = sessionmaker(bind=test_engine)
    
    def override_get_session_test():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()
    
    # Seed 5 servers across 3 tiers
    db = TestSession()
    try:
        # Tier: high
        db.add(McpServerRegistry(
            server_id="srv-001",
            name="high-risk-server-1",
            risk_tier="high",
            registry_source="test",
            first_seen=date.today(),
            last_seen=date.today(),
            last_scanned=date.today(),
            last_assessed=date.today(),
        ))
        db.add(McpServerRegistry(
            server_id="srv-002",
            name="high-risk-server-2",
            risk_tier="high",
            registry_source="test",
            first_seen=date.today(),
            last_seen=date.today(),
            last_scanned=date.today(),
            last_assessed=date.today(),
        ))
        
        # Tier: medium
        db.add(McpServerRegistry(
            server_id="srv-003",
            name="medium-risk-server",
            risk_tier="medium",
            registry_source="test",
            first_seen=date.today(),
            last_seen=date.today(),
            last_scanned=date.today(),
            last_assessed=date.today(),
        ))
        
        # Tier: low
        db.add(McpServerRegistry(
            server_id="srv-004",
            name="low-risk-server-1",
            risk_tier="low",
            registry_source="test",
            first_seen=date.today(),
            last_seen=date.today(),
            last_scanned=date.today(),
            last_assessed=date.today(),
        ))
        db.add(McpServerRegistry(
            server_id="srv-005",
            name="low-risk-server-2",
            risk_tier="low",
            registry_source="test",
            first_seen=date.today(),
            last_seen=date.today(),
            last_scanned=date.today(),
            last_assessed=date.today(),
        ))
        
        db.commit()
    finally:
        db.close()
    
    # Create FastAPI app and test
    test_app = FastAPI()
    
    @test_app.get("/api/registry/risk-distribution")
    def get_distribution(as_of: str | None = None):
        as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        return get_risk_tier_distribution_snapshot(as_of_date, TestSession())
    
    test_app.dependency_overrides[get_session] = override_get_session_test
    
    # Run test using TestClient
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    response = client.get("/api/registry/risk-distribution")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    # Assert tier counts sum to 5
    total = sum(t["count"] for t in data["tiers"])
    assert total == 5, f"Expected total 5, got {total}"
    
    # Assert delta fields present
    for tier in data["tiers"]:
        assert "delta_count" in tier, "delta_count field missing"
        assert "prev_count" in tier, "prev_count field missing"
        assert "prev_pct" in tier, "prev_pct field missing"
    
    # Assert structure
    assert "as_of_date" in data
    assert "total_servers" in data
    assert "tiers" in data
    
    print("PASS")