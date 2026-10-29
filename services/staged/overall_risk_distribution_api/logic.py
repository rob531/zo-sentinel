from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import desc, func, select

from app.db import get_session
from app.models import McpLlmAxisScore


_ALLOWED_BANDS = {10, 25, 50}
_AXIS_NAME = "overall_risk"


@contextmanager
def _session_scope() -> Iterator[Any]:
    resource = get_session()
    if hasattr(resource, "__enter__"):
        with resource as session:
            yield session
        return

    session = next(resource)
    try:
        yield session
    finally:
        close = getattr(resource, "close", None)
        if close is not None:
            close()
        else:
            next(resource, None)


def _rounded_score(value: Any) -> int:
    if value is None:
        return 0
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = 0
    return max(0, min(100, score))


def _band_for_score(score: int, band: int) -> tuple[float, float]:
    number_of_bands = (100 + band - 1) // band
    index = min(score // band, number_of_bands - 1)
    start = index * band
    return float(start), float(min(start + band, 100))


def compute_distribution(band: int, session: Any = None, **kwargs: Any) -> dict:
    """Return the latest overall-risk score distribution for all servers."""
    if band not in _ALLOWED_BANDS:
        raise ValueError("band must be one of: 10, 25, 50")

    if session is None:
        session = kwargs.get("db")

    def run(session: Any) -> dict:
        ranked = (
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.escalated,
                func.row_number()
                .over(
                    partition_by=McpLlmAxisScore.server_id,
                    order_by=(
                        desc(McpLlmAxisScore.scored_at),
                        desc(McpLlmAxisScore.id),
                    ),
                )
                .label("row_number"),
            )
            .where(McpLlmAxisScore.axis_name == _AXIS_NAME)
            .subquery()
        )

        rows = session.execute(
            select(
                ranked.c.server_id,
                ranked.c.p_top,
                ranked.c.escalated,
            ).where(ranked.c.row_number == 1)
        ).all()

        buckets: dict[tuple[float, float], list[int]] = {}
        for row in rows:
            start, end = _band_for_score(_rounded_score(row.p_top), band)
            bucket = buckets.setdefault((start, end), [0, 0])
            bucket[0] += 1
            if bool(row.escalated):
                bucket[1] += 1

        series = [
            {
                "band_start": start,
                "band_end": end,
                "server_count": counts[0],
                "escalated_count": counts[1],
            }
            for (start, end), counts in sorted(buckets.items())
        ]

        return {
            "band": band,
            "total_servers": len(rows),
            "series": series,
        }

    if session is not None:
        return run(session)

    with _session_scope() as owned_session:
        return run(owned_session)