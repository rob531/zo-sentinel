"""logic.py -- corpus growth computation over the real application data layer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AskCorpusDoc, McpServerRegistry


def _normalise_days(days: int) -> int:
    """Return a safe number of calendar days for the computation."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, 365))


def _date_string(value: Any) -> str:
    """Convert a database date expression result to an ISO calendar date."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def get_corpus_growth(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute corpus growth for the requested calendar-day window.

    The window includes today and the preceding ``days - 1`` calendar days.
    All database access uses the real ORM models and no table writes are
    performed.
    """
    day_count = _normalise_days(days)
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=day_count - 1)

    date_expression = func.date(AskCorpusDoc.indexed_at)
    date_filter = date_expression.between(start_date, today)

    daily_rows = db.execute(
        select(
            date_expression.label("calendar_date"),
            func.count(AskCorpusDoc.server_id).label("documents"),
            func.count(func.distinct(AskCorpusDoc.server_id)).label(
                "distinct_servers"
            ),
        )
        .where(date_filter)
        .group_by(date_expression)
        .order_by(date_expression.asc())
    ).all()

    series: List[Dict[str, Any]] = [
        {
            "date": _date_string(row.calendar_date),
            "documents": int(row.documents or 0),
            "distinct_servers": int(row.distinct_servers or 0),
        }
        for row in daily_rows
    ]

    totals = db.execute(
        select(
            func.count(AskCorpusDoc.server_id).label("total_documents"),
            func.count(func.distinct(AskCorpusDoc.server_id)).label(
                "unique_servers"
            ),
        ).where(date_filter)
    ).one()

    top_rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            McpServerRegistry.name,
            func.count(AskCorpusDoc.server_id).label("documents"),
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == AskCorpusDoc.server_id,
        )
        .where(date_filter)
        .group_by(AskCorpusDoc.server_id, McpServerRegistry.name)
        .order_by(
            func.count(AskCorpusDoc.server_id).desc(),
            AskCorpusDoc.server_id.asc(),
        )
        .limit(10)
    ).all()

    top_servers = [
        {
            "server_id": str(row.server_id),
            "name": str(row.name or row.server_id),
            "documents": int(row.documents or 0),
        }
        for row in top_rows
    ]

    return {
        "days": day_count,
        "series": series,
        "total_documents": int(totals.total_documents or 0),
        "unique_servers": int(totals.unique_servers or 0),
        "top_servers": top_servers,
    }