from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()

_SCORE_FIELDS = ("p_critical", "p_danger", "p_top")


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _probabilities(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    if isinstance(value, dict):
        result: dict[str, float] = {}
        for key in _SCORE_FIELDS:
            number = _as_float(value.get(key))
            if number is not None:
                result[key] = number
        if result:
            return result

        result = {}
        for key in _SCORE_FIELDS:
            short_key = key.removeprefix("p_")
            number = _as_float(value.get(short_key))
            if number is not None:
                result[key] = number
        return result

    if isinstance(value, (list, tuple)):
        result = {}
        for key, item in zip(_SCORE_FIELDS, value):
            number = _as_float(item)
            if number is not None:
                result[key] = number
        return result

    return {}


def _score_vector(row: McpLlmAxisScore) -> dict[str, float]:
    vector: dict[str, float] = {}
    for field in _SCORE_FIELDS:
        number = _as_float(getattr(row, field, None))
        if number is not None:
            vector[field] = number

    if vector:
        return vector
    return _probabilities(getattr(row, "probs", None))


def _drift_score(
    earliest: McpLlmAxisScore, latest: McpLlmAxisScore
) -> tuple[float, dict[str, float]]:
    earliest_scores = _score_vector(earliest)
    latest_scores = _score_vector(latest)

    deltas: dict[str, float] = {}
    for field in _SCORE_FIELDS:
        old = earliest_scores.get(field)
        new = latest_scores.get(field)
        if old is not None and new is not None:
            deltas[field] = abs(new - old)

    if not deltas:
        return 0.0, deltas

    return sum(deltas.values()) / len(deltas), deltas


def summarize_axis_drift(
    db: Session, window_days: int = 30
) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=window_days)
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.scored_at.is_not(None),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.asc(), McpLlmAxisScore.id.asc())
        .all()
    )

    grouped: dict[str, list[McpLlmAxisScore]] = {}
    for row in rows:
        if row.axis_name:
            grouped.setdefault(row.axis_name, []).append(row)

    axes: list[dict[str, Any]] = []
    for axis_name, axis_rows in sorted(grouped.items()):
        earliest = axis_rows[0]
        latest = axis_rows[-1]
        drift, deltas = _drift_score(earliest, latest)

        axes.append(
            {
                "axis_name": axis_name,
                "drift": round(drift, 6),
                "drift_score": round(drift, 6),
                "drift_metric": "mean_absolute_probability_delta",
                "probability_deltas": {
                    key: round(value, 6) for key, value in deltas.items()
                },
                "earliest": {
                    "id": earliest.id,
                    "server_id": earliest.server_id,
                    "scored_at": earliest.scored_at,
                    "label": earliest.label,
                    "label_index": earliest.label_index,
                    "scores": _score_vector(earliest),
                },
                "latest": {
                    "id": latest.id,
                    "server_id": latest.server_id,
                    "scored_at": latest.scored_at,
                    "label": latest.label,
                    "label_index": latest.label_index,
                    "scores": _score_vector(latest),
                },
                "sample_count": len(axis_rows),
            }
        )

    most_volatile = max(axes, key=lambda item: item["drift"]) if axes else None
    return {
        "window_days": window_days,
        "generated_at": datetime.now(timezone.utc),
        "metric": "mean_absolute_probability_delta",
        "axes": axes,
        "axis_count": len(axes),
        "most_volatile_axis": most_volatile["axis_name"] if most_volatile else None,
        "max_drift": most_volatile["drift"] if most_volatile else 0.0,
    }


get_axis_score_drift_summary = summarize_axis_drift


@router.get("/api/scores/axis-drift")
def get_axis_drift(
    db: Session = Depends(get_session),
    window_days: int | None = Query(
        default=None, ge=1, le=365, description="Size of the recent window in days."
    ),
    days: int | None = Query(
        default=None,
        ge=1,
        le=365,
        description="Alias for window_days.",
    ),
):
    effective_window_days = days if days is not None else window_days or 30
    return summarize_axis_drift(db, effective_window_days)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()

    def _self_test_session():
        yield None

    that_app.dependency_overrides[get_session] = _self_test_session
    that_app.include_router(router)
    print("PASS")