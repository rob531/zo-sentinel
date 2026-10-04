"""logic.py -- scoring run delta computation over the real data layer."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


def _direction_flag(p_top: float) -> str:
    if p_top >= 0.75:
        return "high"
    elif p_top >= 0.5:
        return "medium"
    else:
        return "low"


def compute_scoring_deltas(db: Session, server_id: str, days: int) -> Dict:
    """
    Compute p_top deltas per axis for a server over the given lookback window.
    Returns the most-recent score minus the oldest score per axis, plus
    direction-change and escalation flags.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id == server_id)
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.asc())
        )
        .scalars()
        .all()
    )

    by_axis: Dict[str, List[McpLlmAxisScore]] = {}
    for row in rows:
        by_axis.setdefault(row.axis_name, []).append(row)

    axes_out = []
    total_escalated = 0
    total_direction_changes = 0

    for axis_name, scores in by_axis.items():
        if len(scores) < 2:
            continue
        oldest = scores[0]
        newest = scores[-1]

        p_before = float(oldest.p_top) if oldest.p_top is not None else 0.0
        p_after = float(newest.p_top) if newest.p_top is not None else 0.0
        delta = p_after - p_before

        dir_before = _direction_flag(p_before)
        dir_after = _direction_flag(p_after)
        direction_changed = dir_before != dir_after

        escalated = bool(newest.escalated) and not bool(oldest.escalated)

        if direction_changed:
            total_direction_changes += 1
        if escalated:
            total_escalated += 1

        axes_out.append({
            "axis_name": axis_name,
            "p_top_before": p_before,
            "p_top_after": p_after,
            "delta": round(delta, 6),
            "direction_changed": direction_changed,
            "escalated": escalated,
        })

    return {
        "server_id": server_id,
        "days": days,
        "runs_compared": len(axes_out),
        "axes": axes_out,
        "total_escalated_axes": total_escalated,
        "total_direction_changes": total_direction_changes,
    }
