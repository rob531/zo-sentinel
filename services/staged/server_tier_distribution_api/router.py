# services/staged/server_tier_distribution_api/logic.py
from typing import List, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def get_tier_distribution(session: Session) -> Dict[str, Any]:
    """
    Query mcp_server_registry, group by risk_tier, return distribution.
    """
    result = session.execute(
        text("""
            SELECT risk_tier, COUNT(*) as count
            FROM mcp_server_registry
            GROUP BY risk_tier
            ORDER BY risk_tier
        """)
    )
    rows = result.fetchall()
    
    total = sum(row.count for row in rows)
    
    tiers = []
    for row in rows:
        tier_name = row.risk_tier if row.risk_tier else "unknown"
        count = row.count
        pct = (count / total * 100) if total > 0 else 0.0
        tiers.append({
            "tier": tier_name,
            "count": count,
            "pct": round(pct, 2)
        })
    
    return {
        "tiers": tiers,
        "total": total
    }