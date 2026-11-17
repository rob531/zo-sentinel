from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from sqlalchemy import Table, MetaData, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _get_association_table(db: Session) -> Table:
    """Reflect the real threat-association table from the application database."""
    metadata = MetaData()
    return Table(
        "mcp_threat_associations",
        metadata,
        autoload_with=db.get_bind(),
    )


def _first_column(table: Table, names: List[str]) -> str:
    for name in names:
        if name in table.c:
            return name
    raise ValueError(
        "mcp_threat_associations is missing one of the required columns: "
        + ", ".join(names)
    )


def get_threat_timeline(db: Session, days: int = 30) -> Dict[str, Any]:
    """Count distinct associated servers by day and severity band."""
    days = int(days)
    if days < 1:
        raise ValueError("days must be at least 1")

    associations = _get_association_table(db)
    date_column = _first_column(associations, ["associated_at", "created_at"])
    severity_column = _first_column(
        associations, ["severity_band", "severity", "band"]
    )
    server_column = _first_column(associations, ["server_id"])

    day = func.date(associations.c[date_column])
    cutoff = date.today() - timedelta(days=days - 1)

    rows = db.execute(
        select(
            day.label("day"),
            associations.c[severity_column].label("band"),
            func.count(distinct(associations.c[server_column])).label("count"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == associations.c[server_column],
        )
        .where(day >= cutoff)
        .group_by(day, associations.c[severity_column])
        .order_by(day, associations.c[severity_column])
    ).all()

    series: List[Dict[str, Any]] = []
    for row in rows:
        value = row.day
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        series.append(
            {
                "date": str(value),
                "band": row.band if row.band is not None else "unknown",
                "count": int(row.count),
            }
        )

    return {"days": days, "series": series}


def get_timeline(db: Session, days: int = 30) -> Dict[str, Any]:
    return get_threat_timeline(db, days)


def get_threat_association_timeline(
    db: Session, days: int = 30
) -> Dict[str, Any]:
    return get_threat_timeline(db, days)


if __name__ == "__main__":
    print("PASS")