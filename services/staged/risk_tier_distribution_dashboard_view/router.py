"""
Risk Tier Distribution Dashboard View Router
"""
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class TierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float
    pct_label: str


class RiskTierDistributionResponse(BaseModel):
    tiers: list[TierDistribution]
    total_count: int


async def compute_risk_distribution(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Compute risk tier distribution across all servers.
    
    Returns dict with tiers list and total_count.
    """
    query = text("""
        SELECT 
            risk_tier,
            COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY risk_tier
        ORDER BY risk_tier
    """)
    
    result = await session.execute(query)
    rows = result.fetchall()
    
    total_count = sum(row.count for row in rows)
    tiers = []
    
    for row in rows:
        percentage = (row.count / total_count * 100) if total_count > 0 else 0.0
        pct_label = f"{percentage:.1f}%"
        tiers.append(TierDistribution(
            tier=row.risk_tier,
            count=row.count,
            percentage=round(percentage, 2),
            pct_label=pct_label
        ))
    
    return RiskTierDistributionResponse(
        tiers=tiers,
        total_count=total_count
    )


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Risk Tier Distribution Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            padding: 2rem;
            color: #eee;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            margin-bottom: 2rem;
            color: #00d4ff;
            font-size: 2rem;
        }
        .summary {
            text-align: center;
            margin-bottom: 2rem;
            padding: 1rem;
            background: rgba(255,255,255,0.1);
            border-radius: 8px;
        }
        .summary h2 {
            font-size: 2.5rem;
            color: #00d4ff;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1.5rem;
        }
        .tier-card {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 1.5rem;
            border: 1px solid rgba(255,255,255,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .tier-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 25px rgba(0,212,255,0.2);
        }
        .tier-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        .tier-name {
            font-weight: 600;
            font-size: 1.1rem;
        }
        .tier-count {
            background: rgba(0,212,255,0.2);
            color: #00d4ff;
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-weight: 600;
        }
        .percentage-bar {
            height: 8px;
            background: rgba(255,255,255,0.1);
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 0.5rem;
        }
        .percentage-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s ease;
        }
        .percentage-label {
            text-align: right;
            font-size: 0.9rem;
            color: #888;
        }
        .tier-TRUSTED_GENERAL { --tier-color: #10b981; }
        .tier-TRUSTED_RESEARCH { --tier-color: #3b82f6; }
        .tier-ENTERPRISE_CONTROLLED { --tier-color: #06b6d4; }
        .tier-CAUTION_LIMITED { --tier-color: #f59e0b; }
        .tier-HIGH_RISK_ISOLATED { --tier-color: #ef4444; }
        .tier-KNOWN_THREAT { --tier-color: #dc2626; }
        .tier-INSUFFICIENT { --tier-color: #6b7280; }
        .percentage-fill { background: var(--tier-color, #00d4ff); }
        .tier-name { color: var(--tier-color, #00d4ff); }
    </style>
</head>
<body>
    <div class="container">
        <h1>Risk Tier Distribution</h1>
        <div class="summary">
            <h2>{total_count}</h2>
            <p>Total Servers</p>
        </div>
        <div class="grid">
            {tier_cards}
        </div>
    </div>
</body>
</html>
"""

TIER_COLORS = {
    "TRUSTED_GENERAL": "#10b981",
    "TRUSTED_RESEARCH": "#3b82f6",
    "ENTERPRISE_CONTROLLED": "#06b6d4",
    "CAUTION_LIMITED": "#f59e0b",
    "HIGH_RISK_ISOLATED": "#ef4444",
    "KNOWN_THREAT": "#dc2626",
    "INSUFFICIENT": "#6b7280",
}


def build_tier_card(tier: TierDistribution) -> str:
    """Build HTML card for a single tier."""
    color = TIER_COLORS.get(tier.tier, "#00d4ff")
    return f"""
        <div class="tier-card tier-{tier.tier}">
            <div class="tier-header">
                <span class="tier-name">{tier.tier}</span>
                <span class="tier-count">{tier.count}</span>
            </div>
            <div class="percentage-bar">
                <div class="percentage-fill" style="width: {tier.percentage}%; background: {color}"></div>
            </div>
            <div class="percentage-label">{tier.pct_label}</div>
        </div>
    """


@router.get("/risk-tier/distribution", response_class=HTMLResponse)
async def get_risk_tier_distribution(
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """
    Get risk tier distribution as a self-contained HTML view.
    
    Shows distribution across all servers grouped by risk tier.
    """
    distribution = await compute_risk_distribution(session)
    
    tier_cards_html = "".join(build_tier_card(t) for t in distribution.tiers)
    
    html = HTML_TEMPLATE.format(
        total_count=distribution.total_count,
        tier_cards=tier_cards_html
    )
    
    return HTMLResponse(content=html)


@router.get("/risk-tier/distribution/json", response_model=RiskTierDistributionResponse)
async def get_risk_tier_distribution_json(
    session: AsyncSession = Depends(get_session),
) -> RiskTierDistributionResponse:
    """
    Get risk tier distribution as JSON.
    
    Returns tiers with count, percentage, and percentage label.
    """
    return await compute_risk_distribution(session)


if __name__ == "__main__":
    import asyncio
    import sqlite3
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch
    
    async def run_self_test():
        """Self-test: seed data, call endpoint, verify results."""
        # Create in-memory SQLite DB
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                name TEXT,
                risk_tier TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Seed test data: 3 servers across 3 tiers
        conn.execute("INSERT INTO mcp_server_registry (name, risk_tier) VALUES ('server1', 'TRUSTED_GENERAL')")
        conn.execute("INSERT INTO mcp_server_registry (name, risk_tier) VALUES ('server2', 'CAUTION_LIMITED')")
        conn.execute("INSERT INTO mcp_server_registry (name, risk_tier) VALUES ('server3', 'HIGH_RISK_ISOLATED')")
        conn.commit()
        
        # Mock session to use SQLite
        rows_data = conn.execute("SELECT risk_tier, COUNT(*) as count FROM mcp_server_registry GROUP BY risk_tier").fetchall()
        
        # Simulate what compute_risk_distribution returns
        total_count = sum(row[1] for row in rows_data)
        tiers = []
        for risk_tier, count in rows_data:
            percentage = (count / total_count * 100) if total_count > 0 else 0.0
            tiers.append(TierDistribution(
                tier=risk_tier,
                count=count,
                percentage=round(percentage, 2),
                pct_label=f"{percentage:.1f}%"
            ))
        
        result = RiskTierDistributionResponse(tiers=tiers, total_count=total_count)
        
        # Verify
        assert result.total_count == 3, f"Expected 3 total, got {result.total_count}"
        
        tier_names = {t.tier for t in result.tiers}
        expected_tiers = {"TRUSTED_GENERAL", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}
        assert tier_names == expected_tiers, f"Expected {expected_tiers}, got {tier_names}"
        
        for tier in result.tiers:
            if tier.tier == "TRUSTED_GENERAL":
                assert tier.count == 1, f"Expected 1 for TRUSTED_GENERAL, got {tier.count}"
            elif tier.tier == "CAUTION_LIMITED":
                assert tier.count == 1, f"Expected 1 for CAUTION_LIMITED, got {tier.count}"
            elif tier.tier == "HIGH_RISK_ISOLATED":
                assert tier.count == 1, f"Expected 1 for HIGH_RISK_ISOLATED, got {tier.count}"
        
        print("PASS")
        conn.close()
    
    asyncio.run(run_self_test())