"""
Risk Tier Distribution Summary Service
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class RiskTierSummary(BaseModel):
    tier: int
    count: int
    percentage: float


class RiskDistributionResponse(BaseModel):
    summary: list[RiskTierSummary]


async def _get_tier_counts(
    org_id: Optional[int],
    session: AsyncSession
) -> list[dict]:
    """
    Get count of servers grouped by risk tier.
    """
    # Parameterized query to prevent SQL injection
    if org_id is not None:
        query = text("""
            SELECT 
                McpLlmAxisScore.risk_tier as tier,
                COUNT(DISTINCT McpServerRegistry.server_id) as count
            FROM McpLlmAxisScore
            INNER JOIN McpServerRegistry 
                ON McpLlmAxisScore.server_id = McpServerRegistry.server_id
            WHERE McpServerRegistry.org_id = :org_id
            GROUP BY McpLlmAxisScore.risk_tier
            ORDER BY tier
        """)
        result = await session.execute(query, {"org_id": org_id})
    else:
        query = text("""
            SELECT 
                McpLlmAxisScore.risk_tier as tier,
                COUNT(DISTINCT McpServerRegistry.server_id) as count
            FROM McpLlmAxisScore
            INNER JOIN McpServerRegistry 
                ON McpLlmAxisScore.server_id = McpServerRegistry.server_id
            GROUP BY McpLlmAxisScore.risk_tier
            ORDER BY tier
        """)
        result = await session.execute(query)
    
    rows = result.fetchall()
    return [{"tier": row.tier, "count": row.count} for row in rows]


async def get_risk_distribution_summary(
    org_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """
    Returns summary of risk tier distributions.
    
    Returns:
        dict: {summary: [{tier, count, percentage}]}
    """
    tier_counts = await _get_tier_counts(org_id, session)
    
    if not tier_counts:
        return {"summary": []}
    
    total = sum(tc["count"] for tc in tier_counts)
    
    summary = []
    for tc in tier_counts:
        percentage = (tc["count"] / total * 100) if total > 0 else 0
        summary.append({
            "tier": tc["tier"],
            "count": tc["count"],
            "percentage": round(percentage, 2)
        })
    
    return {"summary": summary}


if __name__ == "__main__":
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    from app.main import app as fastapi_app
    
    async def run_test():
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from sqlalchemy import text
        
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE McpServerRegistry (
                    server_id TEXT PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            await conn.execute(text("""
                CREATE TABLE McpLlmAxisScore (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    risk_tier INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id)
                )
            """))
            
            servers = [
                ("srv-001", 1, "Server Alpha"),
                ("srv-002", 1, "Server Beta"),
                ("srv-003", 1, "Server Gamma"),
                ("srv-004", 1, "Server Delta"),
                ("srv-005", 1, "Server Epsilon"),
                ("srv-006", 2, "Server Zeta"),
                ("srv-007", 2, "Server Eta"),
                ("srv-008", 2, "Server Theta"),
                ("srv-009", 3, "Server Iota"),
                ("srv-010", 3, "Server Kappa"),
            ]
            for server_id, org_id, name in servers:
                await conn.execute(
                    text("INSERT INTO McpServerRegistry VALUES (:s, :o, :n, CURRENT_TIMESTAMP)"),
                    {"s": server_id, "o": org_id, "n": name}
                )
            
            scores = [
                ("srv-001", 1),
                ("srv-002", 1),
                ("srv-003", 2),
                ("srv-004", 2),
                ("srv-005", 3),
                ("srv-006", 3),
                ("srv-007", 3),
                ("srv-008", 4),
                ("srv-009", 5),
                ("srv-010", 5),
            ]
            for server_id, risk_tier in scores:
                await conn.execute(
                    text("INSERT INTO McpLlmAxisScore (server_id, risk_tier) VALUES (:s, :r)"),
                    {"s": server_id, "r": risk_tier}
                )
        
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        
        async with async_session() as session:
            result = await get_risk_distribution_summary(session=session)
        
        await engine.dispose()
        return result
    
    result = asyncio.run(run_test())
    
    assert len(result["summary"]) == 5, f"Expected 5 tiers, got {len(result['summary'])}"
    
    tier_1_entry = next((s for s in result["summary"] if s["tier"] == 1), None)
    assert tier_1_entry is not None, "Tier 1 not found"
    assert tier_1_entry["count"] == 2, f"Expected tier 1 count=2, got {tier_1_entry['count']}"
    
    tier_3_entry = next((s for s in result["summary"] if s["tier"] == 3), None)
    assert tier_3_entry is not None, "Tier 3 not found"
    assert tier_3_entry["count"] == 3, f"Expected tier 3 count=3, got {tier_3_entry['count']}"
    
    print("PASS")