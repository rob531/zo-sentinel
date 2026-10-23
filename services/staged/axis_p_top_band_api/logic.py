from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

BAND_DEFINITIONS = (
    ("0.0-0.2", 0.0, 0.2),
    ("0.2-0.4", 0.2, 0.4),
    ("0.4-0.6", 0.4, 0.6),
    ("0.6-0.8", 0.6, 0.8),
    ("0.8-1.0", 0.8, 1.0),
)


def _band_for_value(value: float) -> str | None:
    """Return the band containing a p_top value."""
    numeric_value = float(value)

    if numeric_value < 0.0 or numeric_value > 1.0:
        return None

    for label, lower, upper in BAND_DEFINITIONS:
        if lower <= numeric_value < upper:
            return label

    # The final band includes its upper endpoint.
    if numeric_value == 1.0:
        return "0.8-1.0"

    return None


def get_p_top_band(
    axis_name: str,
    days: int,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the p_top distribution for an axis over the requested window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(McpLlmAxisScore.p_top)
        .where(
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at)
    )
    rows = db.execute(statement).scalars().all()

    counts = {label: 0 for label, _, _ in BAND_DEFINITIONS}
    for p_top in rows:
        if p_top is None:
            continue
        label = _band_for_value(p_top)
        if label is not None:
            counts[label] += 1

    total = sum(counts.values())
    bands = [
        {
            "band": label,
            "count": counts[label],
            "share": (counts[label] / total) if total else 0.0,
        }
        for label, _, _ in BAND_DEFINITIONS
    ]

    return {
        "axis_name": axis_name,
        "days": days,
        "bands": bands,
        "total": total,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Compatibility aliases for routers and consumers using the service name.
get_axis_top_band = get_p_top_band
get_axis_p_top_band = get_p_top_band
get_p_top_band_distribution = get_p_top_band
get_axis_p_top_band_distribution = get_p_top_band


if __name__ == "__main__":
    # Keep the self-test independent of a live database while exercising the
    # band boundary rules used by the data access function.
    assert _band_for_value(0.0) == "0.0-0.2"
    assert _band_for_value(0.199999) == "0.0-0.2"
    assert _band_for_value(0.2) == "0.2-0.4"
    assert _band_for_value(0.4) == "0.4-0.6"
    assert _band_for_value(0.6) == "0.6-0.8"
    assert _band_for_value(0.8) == "0.8-1.0"
    assert _band_for_value(1.0) == "0.8-1.0"
    print("PASS")