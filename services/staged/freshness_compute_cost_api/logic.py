from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as _app_models


_QUERY = text(
    """
    SELECT surface, payload, computed_at, compute_seconds
    FROM freshness_stats
    WHERE (:surface IS NULL OR surface = :surface)
      AND (:max_seconds IS NULL OR compute_seconds <= :max_seconds)
    ORDER BY compute_seconds DESC, surface ASC
    """
)


def age_to_status(seconds: float) -> str:
    """Return the freshness status for an age expressed in seconds."""
    if seconds < 3600:
        return "fresh"
    if seconds < 86400:
        return "stale"
    return "expired"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    else:
        raise TypeError(f"Unsupported computed_at value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def get_compute_costs(
    db: AsyncSession | None = None,
    surface: str | None = None,
    max_seconds: float | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Read freshness compute costs from the application database.

    The database is queried without database-specific age calculations.  Ages
    are computed in Python from ``computed_at`` so the same logic works with
    both PostgreSQL and SQLite.
    """
    active_session = session if session is not None else db
    if active_session is None:
        raise ValueError("A database session is required")

    result = await active_session.execute(
        _QUERY,
        {
            "surface": surface,
            "max_seconds": max_seconds,
        },
    )

    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for record in result.mappings().all():
        computed_at = _parse_datetime(record["computed_at"])
        age_seconds = max(0.0, (now - computed_at).total_seconds())
        compute_seconds = float(record["compute_seconds"] or 0.0)

        rows.append(
            {
                "surface": str(record["surface"]),
                "computed_at": computed_at,
                "compute_seconds": compute_seconds,
                "age_seconds": age_seconds,
                "status": age_to_status(age_seconds),
            }
        )

    return rows


async def get_freshness_compute_costs(
    db: AsyncSession | None = None,
    surface: str | None = None,
    max_seconds: float | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Alias for :func:`get_compute_costs`."""
    return await get_compute_costs(
        db,
        surface=surface,
        max_seconds=max_seconds,
        session=session,
    )


async def get_compute_cost(
    db: AsyncSession | None = None,
    surface: str | None = None,
    max_seconds: float | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Return one compute-cost record for each matching surface."""
    return await get_compute_costs(
        db,
        surface=surface,
        max_seconds=max_seconds,
        session=session,
    )


async def get_freshness_compute_cost(
    db: AsyncSession | None = None,
    surface: str | None = None,
    max_seconds: float | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Alias for :func:`get_compute_cost`."""
    return await get_compute_cost(
        db,
        surface=surface,
        max_seconds=max_seconds,
        session=session,
    )


def build_compute_cost_response(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the API response payload from compute-cost rows."""
    surfaces = [dict(row) for row in rows]
    total_compute_seconds = sum(
        float(row.get("compute_seconds", 0.0) or 0.0) for row in surfaces
    )
    slowest_surface = surfaces[0]["surface"] if surfaces else None

    return {
        "surfaces": surfaces,
        "total_compute_seconds": float(total_compute_seconds),
        "slowest_surface": slowest_surface,
    }


async def get_compute_cost_summary(
    db: AsyncSession | None = None,
    surface: str | None = None,
    max_seconds: float | None = None,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Return the complete freshness compute-cost API payload."""
    rows = await get_compute_costs(
        db,
        surface=surface,
        max_seconds=max_seconds,
        session=session,
    )
    return build_compute_cost_response(rows)


if __name__ == "__main__":
    assert age_to_status(0) == "fresh"
    assert age_to_status(3599.999) == "fresh"
    assert age_to_status(3600) == "stale"
    assert age_to_status(86399.999) == "stale"
    assert age_to_status(86400) == "expired"
    print("PASS")