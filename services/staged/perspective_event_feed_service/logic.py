from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpServerRegistry, Perspective, PerspectiveEvent
except ImportError:
    import app.models as _app_models

    def _model_for_table(table_name: str) -> Any:
        for candidate in vars(_app_models).values():
            if getattr(candidate, "__tablename__", None) == table_name:
                return candidate
        raise ImportError(f"No SQLAlchemy model is mapped to table {table_name!r}")

    McpServerRegistry = _model_for_table("mcp_server_registry")
    Perspective = _model_for_table("perspectives")
    PerspectiveEvent = _model_for_table("perspective_events")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_perspective_events(
    perspective_id: str,
    limit: int = 50,
    unseen_only: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    """Fetch the tier-change event feed for a saved perspective."""
    perspective_id = str(perspective_id).strip()
    if not perspective_id:
        raise HTTPException(status_code=404, detail="Perspective not found")

    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="limit must be an integer") from exc

    limit = max(1, min(limit, 500))

    perspective_row = session.execute(
        text(
            """
            SELECT id, name
            FROM perspectives
            WHERE id = :perspective_id
            """
        ),
        {"perspective_id": perspective_id},
    ).mappings().first()

    if perspective_row is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    count_row = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN seen = FALSE THEN 1 ELSE 0 END) AS unseen_count
            FROM perspective_events
            WHERE perspective_id = :perspective_id
            """
        ),
        {"perspective_id": perspective_id},
    ).mappings().first()

    total = int((count_row or {}).get("total") or 0)
    unseen_count = int((count_row or {}).get("unseen_count") or 0)

    event_sql = """
        SELECT
            pe.id,
            pe.server_id,
            registry.name AS server_name,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.seen,
            pe.created_at
        FROM perspective_events AS pe
        LEFT JOIN mcp_server_registry AS registry
            ON registry.server_id = pe.server_id
        WHERE pe.perspective_id = :perspective_id
    """
    parameters: dict[str, Any] = {"perspective_id": perspective_id}

    if unseen_only:
        event_sql += " AND pe.seen = FALSE"

    event_sql += """
        ORDER BY pe.created_at DESC, pe.id DESC
        LIMIT :event_limit
    """
    parameters["event_limit"] = limit

    rows = session.execute(text(event_sql), parameters).mappings().all()

    events = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "server_id": row["server_id"],
                "server_name": row["server_name"],
                "change_type": row["change_type"],
                "old_tier": row["old_tier"],
                "new_tier": row["new_tier"],
                "seen": row["seen"],
                "created_at": _serialize_value(row["created_at"]),
            }
        )

    return {
        "perspective_id": perspective_id,
        "perspective_name": perspective_row["name"],
        "total": total,
        "unseen_count": unseen_count,
        "events": events,
    }


if __name__ == "__main__":
    assert callable(fetch_perspective_events)
    assert fetch_perspective_events.__name__ == "fetch_perspective_events"
    print("PASS")