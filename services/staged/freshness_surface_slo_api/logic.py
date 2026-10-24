from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db import get_session
from app.models import FreshnessStat, McpServerRegistry
from fastapi import Depends


_SECONDS_PER_DAY = 24 * 60 * 60


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _payload_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower

    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def get_freshness_surfaces(
    db=Depends(get_session),
    surface: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    freshness_query = db.query(FreshnessStat)
    if surface is not None:
        freshness_query = freshness_query.filter(FreshnessStat.surface == surface)

    freshness_rows = freshness_query.order_by(FreshnessStat.surface).all()
    surfaces: list[dict[str, Any]] = []

    for row in freshness_rows:
        computed_at = _as_utc(row.computed_at)
        age_seconds = max(0.0, (now - computed_at).total_seconds()) if computed_at else 0.0
        surfaces.append(
            {
                "surface": str(row.surface),
                "computed_at": _iso8601(computed_at) if computed_at else None,
                "age_seconds": age_seconds,
                "compute_seconds": float(row.compute_seconds or 0.0),
                "stale": age_seconds > _SECONDS_PER_DAY,
                "payload": _payload_object(row.payload),
            }
        )

    registry_rows = db.query(McpServerRegistry).all()
    assessment_lags: list[float] = []
    never_assessed = 0
    stale_over_24h = 0

    for row in registry_rows:
        last_assessed = _as_utc(row.last_assessed)
        if last_assessed is None:
            never_assessed += 1
            continue

        lag_seconds = max(0.0, (now - last_assessed).total_seconds())
        assessment_lags.append(lag_seconds)
        if lag_seconds > _SECONDS_PER_DAY:
            stale_over_24h += 1

    return {
        "surfaces": surfaces,
        "registry": {
            "total_servers": len(registry_rows),
            "never_assessed": never_assessed,
            "assessment_lag_p50_seconds": _percentile(assessment_lags, 0.50),
            "assessment_lag_p95_seconds": _percentile(assessment_lags, 0.95),
            "stale_over_24h": stale_over_24h,
        },
        "generated_at": _iso8601(now),
    }