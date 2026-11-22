"""logic.py -- the service's data/computation layer.

Provides registry unauthenticated-access reporting over the real application
models.  The caller supplies the application SQLAlchemy session; this module
does not create a database or execute raw SQL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, McpUnauthAccess


def _as_text(value: Any) -> str:
    """Return a JSON-safe string for a registry value."""
    if value is None:
        return ""
    return str(value)


def _as_iso8601(value: Any) -> str:
    """Return a timestamp in ISO-8601 form, tolerating ORM date values."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_unauth_access(
    db: Session,
    source: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return unauthenticated-access rows and per-source counts.

    The registry table is joined with a left join so access records remain
    visible when no corresponding registry row exists.  Counts and the total
    describe all rows matching the optional source filter; only the returned
    server list is limited.
    """
    limit = max(1, min(int(limit), 500))

    row_query = (
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(
            McpUnauthAccess.__table__.join(
                McpServerRegistry.__table__,
                McpUnauthAccess.server_id == McpServerRegistry.server_id,
                isouter=True,
            )
        )
        .order_by(
            McpUnauthAccess.updated_at.desc(),
            McpUnauthAccess.server_id.asc(),
        )
    )
    count_query = select(
        McpUnauthAccess.source,
        func.count(McpUnauthAccess.server_id),
    ).group_by(McpUnauthAccess.source)

    if source is not None:
        row_query = row_query.where(McpUnauthAccess.source == source)
        count_query = count_query.where(McpUnauthAccess.source == source)

    source_rows = db.execute(count_query).all()
    sources = [
        {"source": _as_text(source_name), "count": int(count)}
        for source_name, count in source_rows
    ]

    rows = db.execute(row_query.limit(limit)).all()
    servers = [
        {
            "server_id": _as_text(server_id),
            "name": None if name is None else str(name),
            "risk_tier": None if risk_tier is None else str(risk_tier),
            "url": _as_text(url),
            "unauth_access": _as_text(unauth_access),
            "updated_at": _as_iso8601(updated_at),
        }
        for (
            server_id,
            url,
            unauth_access,
            _row_source,
            updated_at,
            name,
            risk_tier,
        ) in rows
    ]

    total = sum(item["count"] for item in sources)
    return {"sources": sources, "servers": servers, "total": total}


# Keep a descriptive alias for callers that use the service name as the
# computation entry point.
registry_unauth_access = get_unauth_access
list_unauth_access = get_unauth_access