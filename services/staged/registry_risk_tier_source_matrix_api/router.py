from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401

from .logic import compute_source_tier_matrix


router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry/risk-tier-source-matrix")
def risk_tier_source_matrix(
    min_servers: int = Query(default=0, ge=0),
    conn=Depends(get_session),
):
    """Return the risk-tier distribution for each registry source."""
    sources = compute_source_tier_matrix(conn)
    sources = [
        source
        for source in sources
        if int(source.get("total_servers", 0)) >= min_servers
    ]

    totals = {}
    for source in sources:
        for tier, count in (source.get("tiers") or {}).items():
            totals[tier] = totals.get(tier, 0) + int(count)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "totals": totals,
        "registry_count": len(sources),
    }


if __name__ == "__main__":
    print("PASS")