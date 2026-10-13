"""services/staged/never_scored_backlog_report/logic.py"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import McpLlmAxisScore, McpServerRegistry


async def get_never_scored_backlog(session: AsyncSession) -> dict[str, Any]:
    query = text("""
        SELECT 
            sr.registry_source,
            sr.risk_tier,
            COUNT(*) as server_count
        FROM mcp_server_registry sr
        LEFT JOIN mcp_llm_axis_scores ax ON ax.server_id = sr.server_id
        WHERE ax.id IS NULL
        GROUP BY sr.registry_source, sr.risk_tier
        ORDER BY sr.registry_source, sr.risk_tier
    """)
    
    result = await session.execute(query)
    rows = result.fetchall()
    
    sources_dict: dict[str, dict[str, Any]] = {}
    total = 0
    
    for row in rows:
        source = row.registry_source or "unknown"
        tier = row.risk_tier or "unknown"
        count = row.server_count
        
        total += count
        
        if source not in sources_dict:
            sources_dict[source] = {"risk_tiers": {}}
        
        sources_dict[source]["risk_tiers"][tier] = count
    
    sources = []
    for source_name, source_data in sorted(sources_dict.items()):
        count = sum(source_data["risk_tiers"].values())
        pct = round((count / total * 100), 2) if total > 0 else 0.0
        sources.append({
            "source": source_name,
            "count": count,
            "pct": pct,
            "risk_tiers": source_data["risk_tiers"]
        })
    
    return {
        "total": total,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }