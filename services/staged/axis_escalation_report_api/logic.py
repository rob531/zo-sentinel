from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select

from app.db import get_session  # noqa: F401 - establishes the real data-layer dependency
from app.models import McpLlmAxisScore


DEFAULT_DAYS = 30
MAX_DAYS = 365
DEFAULT_TOP_SERVER_LIMIT = 10


def _normalize_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(MAX_DAYS, value))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_axis_escalation_report(
    session: Any,
    days: int = DEFAULT_DAYS,
    top_limit: int = DEFAULT_TOP_SERVER_LIMIT,
) -> Dict[str, Any]:
    """Compute the axis escalation report from the real application data layer."""
    normalized_days = _normalize_days(days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=normalized_days
    )

    statement = select(McpLlmAxisScore).where(
        McpLlmAxisScore.scored_at >= cutoff
    )
    rows = list(session.execute(statement).scalars())

    escalated_rows = [row for row in rows if bool(row.escalated)]
    total_rows = len(rows)
    total_escalations = len(escalated_rows)

    axis_counts: Dict[str, int] = {}
    target_counts: Dict[str, Dict[str, int]] = {}

    for row in escalated_rows:
        axis_name = str(row.axis_name)
        axis_counts[axis_name] = axis_counts.get(axis_name, 0) + 1

        if row.escalated_to is None:
            continue

        target = str(row.escalated_to)
        target_counts.setdefault(axis_name, {})
        target_counts[axis_name][target] = (
            target_counts[axis_name].get(target, 0) + 1
        )

    by_axis: List[Dict[str, Any]] = []
    for axis_name in sorted(axis_counts, key=lambda name: (-axis_counts[name], name)):
        targets = [
            {"escalated_to": target, "count": count}
            for target, count in sorted(
                target_counts.get(axis_name, {}).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        by_axis.append(
            {
                "axis_name": axis_name,
                "count": axis_counts[axis_name],
                "targets": targets,
            }
        )

    ranked_servers = sorted(
        escalated_rows,
        key=lambda row: (
            -float(row.p_top) if row.p_top is not None else float("inf"),
            str(row.server_id),
            str(row.axis_name),
            str(row.escalated_to or ""),
        ),
    )

    try:
        normalized_top_limit = max(0, int(top_limit))
    except (TypeError, ValueError):
        normalized_top_limit = DEFAULT_TOP_SERVER_LIMIT

    top_servers = [
        {
            "server_id": str(row.server_id) if row.server_id is not None else None,
            "axis_name": str(row.axis_name),
            "escalated_to": (
                str(row.escalated_to) if row.escalated_to is not None else None
            ),
            "p_top": float(row.p_top) if row.p_top is not None else None,
        }
        for row in ranked_servers[:normalized_top_limit]
    ]

    escalation_rate_pct = (
        round((total_escalations / total_rows) * 100, 2) if total_rows else 0.0
    )

    return {
        "days": normalized_days,
        "generated_at": _utc_now_iso(),
        "total_escalations": total_escalations,
        "escalation_rate_pct": escalation_rate_pct,
        "by_axis": by_axis,
        "top_servers": top_servers,
    }


def get_axis_escalations(
    session: Any,
    days: int = DEFAULT_DAYS,
    top_limit: int = DEFAULT_TOP_SERVER_LIMIT,
) -> Dict[str, Any]:
    return get_axis_escalation_report(session, days, top_limit)


def build_axis_escalation_report(
    session: Any,
    days: int = DEFAULT_DAYS,
    top_limit: int = DEFAULT_TOP_SERVER_LIMIT,
) -> Dict[str, Any]:
    return get_axis_escalation_report(session, days, top_limit)


def compute_axis_escalations(
    session: Any,
    days: int = DEFAULT_DAYS,
    top_limit: int = DEFAULT_TOP_SERVER_LIMIT,
) -> Dict[str, Any]:
    return get_axis_escalation_report(session, days, top_limit)


if __name__ == "__main__":
    assert _normalize_days(DEFAULT_DAYS) == DEFAULT_DAYS
    assert _normalize_days(0) == 1
    assert _normalize_days(MAX_DAYS + 1) == MAX_DAYS
    assert _normalize_days("invalid") == DEFAULT_DAYS
    print("PASS")