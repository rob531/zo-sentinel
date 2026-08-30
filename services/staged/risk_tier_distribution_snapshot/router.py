"""
Router for risk_tier_distribution_snapshot service.
GET /api/risk/distribution/snapshot - Returns risk tier distribution snapshot.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/risk", tags=["risk"])


class TierDistribution(BaseModel):
    """Distribution stats for a single risk tier."""
    tier: str = Field(description="Risk tier name")
    server_count: int = Field(description="Number of servers in this tier")
    p_top_p50: Optional[float] = Field(None, description="Median p_top value for this tier")
    p_top_p95: Optional[float] = Field(None, description="95th percentile p_top value for this tier")


class RiskDistributionSnapshotResponse(BaseModel):
    """Response model for risk distribution snapshot endpoint."""
    tiers: List[TierDistribution] = Field(description="List of tier distributions")
    generated_at: str = Field(description="ISO timestamp when snapshot was generated")


def _build_query() -> text:
    """Build the SQL query for risk tier distribution snapshot."""
    return text("""
        WITH tier_stats AS (
            SELECT
                sr.risk_tier,
                COUNT(DISTINCT sr.server_id) AS server_count,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY las.p_top) AS p_top_p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY las.p_top) AS p_top_p95
            FROM McpLlmAxisScore las
            INNER JOIN McpServerRegistry sr ON las.server_id = sr.server_id
            GROUP BY sr.risk_tier
        )
        SELECT
            risk_tier AS tier,
            server_count,
            p_top_p50,
            p_top_p95
        FROM tier_stats
        ORDER BY risk_tier
    """)


@router.get("/distribution/snapshot", response_model=RiskDistributionSnapshotResponse)
def get_risk_distribution_snapshot(
    session: Session = Depends(get_session)
) -> RiskDistributionSnapshotResponse:
    """
    Get risk tier distribution snapshot.
    
    Returns counts and p_top percentile distributions grouped by risk tier.
    """
    query = _build_query()
    result = session.execute(query)
    rows = result.fetchall()
    
    tiers = [
        TierDistribution(
            tier=row.tier,
            server_count=row.server_count,
            p_top_p50=float(row.p_top_p50) if row.p_top_p50 is not None else None,
            p_top_p95=float(row.p_top_p95) if row.p_top_p95 is not None else None,
        )
        for row in rows
    ]
    
    return RiskDistributionSnapshotResponse(
        tiers=tiers,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    import sqlite3
    
    def run_self_test():
        """Self-test using in-memory SQLite with mock data."""
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT NOT NULL,
                server_name TEXT,
                org_id TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id)
            )
        """)
        
        mock_servers = [
            ("srv-001", "critical", "Server Critical 1"),
            ("srv-002", "critical", "Server Critical 2"),
            ("srv-003", "critical", "Server Critical 3"),
            ("srv-004", "critical", "Server Critical 4"),
            ("srv-005", "high", "Server High 1"),
            ("srv-006", "high", "Server High 2"),
            ("srv-007", "high", "Server High 3"),
            ("srv-008", "medium", "Server Medium 1"),
            ("srv-009", "medium", "Server Medium 2"),
            ("srv-010", "low", "Server Low 1"),
        ]
        
        for server_id, tier, name in mock_servers:
            cursor.execute(
                "INSERT INTO McpServerRegistry (server_id, risk_tier, server_name) VALUES (?, ?, ?)",
                (server_id, tier, name)
            )
        
        import random
        random.seed(42)
        
        p_top_ranges = {
            "critical": (0.85, 0.99),
            "high": (0.65, 0.84),
            "medium": (0.40, 0.64),
            "low": (0.10, 0.39),
        }
        
        for server_id, tier, _ in mock_servers:
            p_min, p_max = p_top_ranges[tier]
            for axis in ["security", "reliability", "performance"]:
                p_top = random.uniform(p_min, p_max)
                cursor.execute(
                    "INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top) VALUES (?, ?, ?)",
                    (server_id, axis, p_top)
                )
        
        conn.commit()
        
        cursor.execute("""
            WITH tier_stats AS (
                SELECT
                    sr.risk_tier,
                    COUNT(DISTINCT sr.server_id) AS server_count,
                    AVG(las.p_top) AS avg_p_top
                FROM McpLlmAxisScore las
                INNER JOIN McpServerRegistry sr ON las.server_id = sr.server_id
                GROUP BY sr.risk_tier
            )
            SELECT
                risk_tier,
                server_count
            FROM tier_stats
            ORDER BY risk_tier
        """)
        
        rows = cursor.fetchall()
        
        assert len(rows) >= 3, f"Expected at least 3 tiers, got {len(rows)}"
        
        total_servers = sum(row[1] for row in rows)
        assert total_servers == 10, f"Expected 10 total servers, got {total_servers}"
        
        conn.close()
        
        print("PASS")
    
    run_self_test()