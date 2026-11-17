from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

CONFIDENCE_BUCKETS = {
    "0.0-0.2": 0,
    "0.2-0.4": 0,
    "0.4-0.6": 0,
    "0.6-0.8": 0,
    "0.8-1.0": 0,
}


def _numeric_values(value: Any) -> list[float]:
    if value is None:
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if isinstance(value, Mapping):
        value = value.values()

    if isinstance(value, (str, bytes)):
        return []

    if not isinstance(value, Iterable):
        return []

    values: list[float] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("probability", item.get("prob", item.get("value")))
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def _confidence_value(score: McpLlmAxisScore) -> float:
    values = _numeric_values(getattr(score, "probs", None))
    if values:
        return sum(values) / len(values)

    p_top = getattr(score, "p_top", None)
    try:
        value = float(p_top)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _bucket_for(value: float) -> str:
    if value < 0.2:
        return "0.0-0.2"
    if value < 0.4:
        return "0.2-0.4"
    if value < 0.6:
        return "0.4-0.6"
    if value < 0.8:
        return "0.6-0.8"
    return "0.8-1.0"


def _average(values: list[float | None]) -> float:
    usable = [value for value in values if value is not None]
    if not usable:
        return 0.0
    return sum(usable) / len(usable)


def get_axes(session: Session) -> list[dict[str, Any]]:
    rows = (
        session.execute(select(McpLlmAxisScore).order_by(McpLlmAxisScore.id))
        .scalars()
        .all()
    )

    grouped: dict[str, list[McpLlmAxisScore]] = {axis: [] for axis in AXIS_NAMES}
    for row in rows:
        axis_name = getattr(row, "axis_name", None)
        if axis_name in grouped:
            grouped[axis_name].append(row)

    result: list[dict[str, Any]] = []
    for axis_name in AXIS_NAMES:
        axis_rows = grouped[axis_name]
        buckets = dict(CONFIDENCE_BUCKETS)

        for row in axis_rows:
            buckets[_bucket_for(_confidence_value(row))] += 1

        result.append(
            {
                "axis_name": axis_name,
                "row_count": len(axis_rows),
                "avg_p_top": _average(
                    [getattr(row, "p_top", None) for row in axis_rows]
                ),
                "avg_p_critical": _average(
                    [getattr(row, "p_critical", None) for row in axis_rows]
                ),
                "avg_p_danger": _average(
                    [getattr(row, "p_danger", None) for row in axis_rows]
                ),
                "confidence_buckets": buckets,
            }
        )

    return result


def get_axis_entropy(session: Session) -> list[dict[str, Any]]:
    return get_axes(session)


def get_axis_entropy_summary(session: Session) -> dict[str, Any]:
    return {
        "axes": get_axes(session),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _self_test() -> None:
    assert _bucket_for(0.0) == "0.0-0.2"
    assert _bucket_for(0.2) == "0.2-0.4"
    assert _bucket_for(0.4) == "0.4-0.6"
    assert _bucket_for(0.6) == "0.6-0.8"
    assert _bucket_for(0.8) == "0.8-1.0"
    assert _bucket_for(1.0) == "0.8-1.0"
    assert _numeric_values('{"a": 0.2, "b": 0.8}') == [0.2, 0.8]
    print("PASS")


if __name__ == "__main__":
    _self_test()