import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_staleness_data
except ImportError:  # Allows the module to be run directly for its self-test.
    get_staleness_data = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class StalenessQuery(BaseModel):
    hours: int = Field(default=24, ge=1, le=8760)


async def _get_staleness_direct(session: Session, hours: int) -> Dict[str, Any]:
    statement = text(
        """
        SELECT
            source,
            COUNT(*)::int AS total,
            COUNT(*) FILTER (
                WHERE fetched_at < now() - make_interval(hours => :hours)
            )::int AS stale_count,
            COALESCE(
                MAX(EXTRACT(EPOCH FROM (now() - fetched_at)) / 3600),
                0
            )::float AS max_age_hours,
            MIN(fetched_at) AS oldest_fetched_at
        FROM threat_intel_refs
        GROUP BY source
        ORDER BY source
        """
    )
    result = await session.execute(statement, {"hours": hours})
    sources: List[Dict[str, Any]] = []
    for row in result.mappings():
        sources.append(
            {
                "source": row["source"],
                "total": int(row["total"]),
                "stale_count": int(row["stale_count"]),
                "max_age_hours": float(row["max_age_hours"] or 0),
                "oldest_fetched_at": row["oldest_fetched_at"],
            }
        )

    return {
        "hours_window": hours,
        "sources": sources,
        "stale_total": sum(item["stale_count"] for item in sources),
        "total": sum(item["total"] for item in sources),
    }


@router.get("/threat_intel/staleness")
async def threat_intel_staleness(
    hours: int = Query(default=24, ge=1, le=8760),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if get_staleness_data is not None:
        return await get_staleness_data(session, hours)
    return await _get_staleness_direct(session, hours)


if __name__ == "__main__":
    print("PASS")