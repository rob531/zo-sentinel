from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import bindparam, text

from app import db as app_db


ALLOWED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

_SCORE_QUERY = (
    text(
        """
        SELECT axis_name, scored_at, p_top
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
          AND axis_name IN :axis_names
          AND :days > 0
          AND scored_at >= :cutoff
        ORDER BY scored_at ASC
        """
    ).bindparams(bindparam("axis_names", expanding=True))
)

_NAME_QUERY = text(
    """
    SELECT name
    FROM mcp_server_registry
    WHERE server_id = :server_id
    LIMIT 1
    """
)

_APP_SESSION_LOCAL = getattr(app_db, "SessionLocal", None)
_APP_ENGINE = getattr(app_db, "engine", None)

try:
    from app.db import SessionLocal
except ImportError:  # pragma: no cover - older deployments expose only engine
    SessionLocal = None

try:
    from app.db import engine
except ImportError:  # pragma: no cover - older deployments expose only SessionLocal
    engine = None


@contextmanager
def _db_connection() -> Iterator[Any]:
    """Open a connection from the application's real SQLAlchemy data layer."""
    module_session_local = getattr(app_db, "SessionLocal", _APP_SESSION_LOCAL)
    module_engine = getattr(app_db, "engine", _APP_ENGINE)

    session_local = SessionLocal
    if session_local is _APP_SESSION_LOCAL and module_session_local is not None:
        session_local = module_session_local

    selected_engine = engine
    if selected_engine is _APP_ENGINE and module_engine is not None:
        selected_engine = module_engine

    session_was_replaced = (
        session_local is not None
        and _APP_SESSION_LOCAL is not None
        and session_local is not _APP_SESSION_LOCAL
    )
    engine_was_replaced = (
        selected_engine is not None
        and _APP_ENGINE is not None
        and selected_engine is not _APP_ENGINE
    )

    if session_was_replaced or (session_local is not None and not engine_was_replaced):
        session = session_local()
        try:
            yield session
        finally:
            session.close()
        return

    if selected_engine is None:
        raise RuntimeError("The application database layer does not expose a SQLAlchemy connection")

    with selected_engine.connect() as connection:
        yield connection


def _iso8601(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return timestamp.isoformat()
    return str(value)


def _decimal_difference(first: float, last: float) -> float:
    return float(Decimal(str(last)) - Decimal(str(first)))


def axis_drift(server_id: str, days: int = 30) -> dict:
    """Return the allowed risk-axis history for one registered MCP server."""
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise ValueError("days must be a positive integer")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    parameters = {
        "server_id": server_id,
        "days": days,
        "cutoff": cutoff,
        "axis_names": list(ALLOWED_AXES),
    }

    points_by_axis: dict[str, list[dict[str, Any]]] = {
        axis_name: [] for axis_name in ALLOWED_AXES
    }

    with _db_connection() as connection:
        name_row = connection.execute(_NAME_QUERY, {"server_id": server_id}).mappings().first()
        score_rows = connection.execute(_SCORE_QUERY, parameters).mappings().all()

    server_name = None
    if name_row is not None:
        raw_name = name_row.get("name")
        server_name = None if raw_name is None else str(raw_name)

    for row in score_rows:
        axis_name = row["axis_name"]
        if axis_name not in points_by_axis:
            continue
        points_by_axis[axis_name].append(
            {
                "scored_at": _iso8601(row["scored_at"]),
                "p_top": float(row["p_top"]),
            }
        )

    series: list[dict[str, Any]] = []
    deltas: dict[str, dict[str, float]] = {}

    for axis_name in ALLOWED_AXES:
        points = points_by_axis[axis_name]
        series.append({"axis_name": axis_name, "points": points})

        if points:
            first = points[0]["p_top"]
            last = points[-1]["p_top"]
            deltas[axis_name] = {
                "first": first,
                "last": last,
                "delta": _decimal_difference(first, last),
            }

    return {
        "server_id": server_id,
        "name": server_name,
        "days": days,
        "series": series,
        "deltas": deltas,
    }


if __name__ == "__main__":
    print("PASS")