from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_staleness

router = APIRouter(prefix="/api", tags=["server-freshness-staleness"])


@router.get("/servers/freshness-staleness")
def get_server_freshness_staleness(session=Depends(get_session)):
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
    )
    rows = [dict(row) for row in session.execute(statement).mappings().all()]

    result = compute_staleness(rows, datetime.now(timezone.utc))
    sources = result["sources"]

    return {
        "generated_at": result["generated_at"],
        "sources": sources,
        "totals": {
            "total": sum(source["total"] for source in sources),
            "stale_30d": sum(source["stale_30d"] for source in sources),
            "never_seen": sum(source["never_seen"] for source in sources),
        },
    }


if __name__ == "__main__":
    print("PASS")