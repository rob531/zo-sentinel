"""Risk-tier movement computation over the real application data layer."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _tier(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def _score_rows(db: Session, start: date, end: date) -> Iterable[Tuple[Any, ...]]:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)
    return db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.label,
            McpLlmAxisScore.scored_at,
        ).where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= start_dt,
            McpLlmAxisScore.scored_at < end_dt,
        )
    ).all()


def get_risk_tier_movement(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return daily risk-tier transitions and the largest individual movers."""
    days = max(1, min(365, int(days)))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)

    registry_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.first_seen,
        )
    ).all()

    registry: Dict[str, Dict[str, Any]] = {}
    for server_id, name, risk_tier, first_seen in registry_rows:
        if server_id is None:
            continue
        registry[str(server_id)] = {
            "name": _tier(name),
            "risk_tier": _tier(risk_tier),
            "first_seen": _as_date(first_seen),
        }

    # Keep the latest scored row for each server and calendar day.
    latest: Dict[Tuple[str, date], Tuple[datetime, str]] = {}
    for server_id, label, scored_at in _score_rows(db, start, end):
        if server_id is None:
            continue
        server_key = str(server_id)
        if server_key not in registry:
            continue
        scored_day = _as_date(scored_at)
        if scored_day is None:
            continue
        timestamp = scored_at
        if not isinstance(timestamp, datetime):
            timestamp = datetime.min
        key = (server_key, scored_day)
        previous = latest.get(key)
        if previous is None or timestamp > previous[0]:
            latest[key] = (timestamp, _tier(label))

    by_server: Dict[str, Dict[date, str]] = {}
    for (server_key, scored_day), (_, tier) in latest.items():
        by_server.setdefault(server_key, {})[scored_day] = tier

    server_dates = sorted(registry)
    movements: List[Dict[str, Any]] = []
    actual_changes: List[Dict[str, Any]] = []
    transition_counts: Counter[Tuple[Optional[str], str, str]] = Counter()

    for server_key in server_dates:
        scores = by_server.get(server_key, {})
        score_dates = sorted(scores)

        if score_dates:
            first_day = score_dates[0]
        else:
            first_seen = registry[server_key]["first_seen"]
            first_day = max(start, first_seen) if first_seen else start

        if first_day > end:
            continue

        previous_tier: Optional[str] = None
        previous_day: Optional[date] = None
        current_day = first_day
        while current_day <= end:
            tier = scores.get(current_day, registry[server_key]["risk_tier"])
            tier = _tier(tier)

            if previous_tier is None:
                transition = {
                    "date": current_day.isoformat(),
                    "from_tier": None,
                    "to_tier": tier,
                    "server_id": server_key,
                    "name": registry[server_key]["name"],
                    "delta_days": 0,
                }
                movements.append(transition)
                transition_counts[(None, tier, current_day.isoformat())] += 1
            elif previous_tier != tier:
                transition = {
                    "date": current_day.isoformat(),
                    "from_tier": previous_tier,
                    "to_tier": tier,
                    "server_id": server_key,
                    "name": registry[server_key]["name"],
                    "delta_days": (current_day - previous_day).days
                    if previous_day is not None
                    else 0,
                }
                movements.append(transition)
                transition_counts[
                    (previous_tier, tier, current_day.isoformat())
                ] += 1
                actual_changes.append(transition)

            previous_tier = tier
            previous_day = current_day
            current_day += timedelta(days=1)

    series_counts: Counter[Tuple[str, Optional[str], str]] = Counter()
    for movement in movements:
        series_counts[
            (movement["date"], movement["from_tier"], movement["to_tier"])
        ] += 1

    series = [
        {
            "date": movement_date,
            "from_tier": from_tier,
            "to_tier": to_tier,
            "count": int(count),
        }
        for (movement_date, from_tier, to_tier), count in sorted(
            series_counts.items(),
            key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
        )
    ]

    actual_changes.sort(
        key=lambda movement: (
            -transition_counts[
                (
                    movement["from_tier"],
                    movement["to_tier"],
                    movement["date"],
                )
            ],
            movement["date"],
            movement["server_id"],
        ),
        reverse=False,
    )
    # The transition count and changed date are descending; server id is only a
    # deterministic tie-breaker.
    actual_changes.sort(
        key=lambda movement: movement["server_id"],
    )
    actual_changes.sort(
        key=lambda movement: (
            -transition_counts[
                (
                    movement["from_tier"],
                    movement["to_tier"],
                    movement["date"],
                )
            ],
        ),
    )
    actual_changes.sort(
        key=lambda movement: movement["date"],
        reverse=True,
    )

    top_movers = [
        {
            "server_id": movement["server_id"],
            "name": movement["name"],
            "from_tier": movement["from_tier"],
            "to_tier": movement["to_tier"],
            "changed_on": movement["date"],
            "delta_days": int(movement["delta_days"]),
        }
        for movement in actual_changes[:50]
    ]

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": series,
        "top_movers": top_movers,
    }


def calculate_risk_tier_movement(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the calculation-oriented name."""
    return get_risk_tier_movement(db, days)