from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import calculate_concentration as _calculate_concentration
except ImportError:
    _calculate_concentration: Optional[Callable[..., Any]] = None

router = APIRouter(prefix="/api")


def _calculate_concentration_inline(session: Session, limit: int) -> dict[str, Any]:
    association_rows = session.execute(
        text(
            """
            SELECT
                server_id,
                COUNT(*) AS association_count
            FROM mcp_threat_associations
            GROUP BY server_id
            """
        )
    ).all()

    counts = {
        str(row.server_id): int(row.association_count)
        for row in association_rows
        if row.server_id is not None
    }

    registry_rows = session.execute(
        text(
            """
            SELECT server_id, name, risk_tier, verdict
            FROM mcp_server_registry
            """
        )
    ).all()

    servers = {
        str(row.server_id): {
            "server_id": str(row.server_id),
            "name": row.name,
            "risk_tier": row.risk_tier,
            "verdict": row.verdict,
        }
        for row in registry_rows
        if row.server_id is not None
    }

    total_associations = sum(counts.values())
    distinct_servers = len(counts)

    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )

    top_share_pct = 0.0
    if total_associations:
        top_count = sum(count for _, count in ranked[:limit])
        top_share_pct = round((top_count / total_associations) * 100.0, 6)

    hhi = 0.0
    if total_associations:
        hhi = round(
            sum((count / total_associations) ** 2 for _, count in ranked),
            6,
        )

    top_servers = []
    for server_id, count in ranked[:limit]:
        server = servers.get(
            server_id,
            {
                "server_id": server_id,
                "name": None,
                "risk_tier": None,
                "verdict": None,
            },
        )
        top_servers.append(
            {
                "server_id": server["server_id"],
                "name": server["name"],
                "risk_tier": server["risk_tier"],
                "count": count,
                "pct": round((count / total_associations) * 100.0, 6)
                if total_associations
                else 0.0,
            }
        )

    return {
        "total_associations": total_associations,
        "distinct_servers": distinct_servers,
        "top_share_pct": top_share_pct,
        "hhi": hhi,
        "top_servers": top_servers,
    }


@router.get("/threat/concentration")
def threat_concentration(
    limit: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if _calculate_concentration is not None:
        try:
            return _calculate_concentration(session, limit)
        except TypeError:
            pass

    return _calculate_concentration_inline(session, limit)