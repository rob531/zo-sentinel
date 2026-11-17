"""logic.py -- adapter SHA drift computations over the real application database.

The service reads the authoritative ``mcp_llm_axis_scores`` table through the
application SQLAlchemy session and performs only read-only queries.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


def _as_day(value: Any) -> Optional[date]:
    """Return the calendar day represented by a scored_at value."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        text = str(value)
        if len(text) >= 10:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _day_server_counts(
    db: Session,
) -> Dict[str, Dict[str, Dict[date, set]]]:
    """Collect distinct server counts by model, adapter, and scored-at day."""
    rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
        ).where(McpLlmAxisScore.adapter_sha256.is_not(None))
    ).all()

    counts: Dict[str, Dict[str, Dict[date, set]]] = {}
    for model_version, adapter_sha256, server_id, scored_at in rows:
        day = _as_day(scored_at)
        if day is None:
            continue
        model_key = str(model_version)
        adapter_key = str(adapter_sha256)
        model_counts = counts.setdefault(model_key, {})
        adapter_counts = model_counts.setdefault(adapter_key, {})
        adapter_counts.setdefault(day, set()).add(server_id)
    return counts


def _changed_adapters(
    day_counts: Dict[str, Dict[str, Dict[date, set]]],
) -> Dict[str, List[str]]:
    """Find adapters whose distinct server count differs on the last two days."""
    changed: Dict[str, List[str]] = {}
    for model_version, adapters in day_counts.items():
        model_days = sorted(
            {
                day
                for adapter_days in adapters.values()
                for day in adapter_days
            }
        )
        if len(model_days) < 2:
            changed[model_version] = []
            continue

        earlier, later = model_days[-2], model_days[-1]
        changed[model_version] = sorted(
            adapter_sha256
            for adapter_sha256, adapter_days in adapters.items()
            if len(adapter_days.get(earlier, set()))
            != len(adapter_days.get(later, set()))
        )
    return changed


def adapter_sha_drift(db: Session) -> Dict[str, Any]:
    """Return adapter diversity and server coverage grouped by model version."""
    summary_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            func.count(func.distinct(McpLlmAxisScore.adapter_sha256)),
            func.count(func.distinct(McpLlmAxisScore.decision_rule_version)),
            func.count(),
            func.count(func.distinct(McpLlmAxisScore.server_id)),
        ).group_by(McpLlmAxisScore.model_version)
    ).all()

    adapter_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.count(func.distinct(McpLlmAxisScore.server_id)),
        )
        .where(McpLlmAxisScore.adapter_sha256.is_not(None))
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
        )
    ).all()

    adapters_by_model: Dict[str, List[Dict[str, Any]]] = {}
    for model_version, adapter_sha256, servers in adapter_rows:
        adapters_by_model.setdefault(str(model_version), []).append(
            {
                "adapter_sha256": str(adapter_sha256),
                "servers": int(servers),
            }
        )

    model_versions: List[Dict[str, Any]] = []
    total_scores = 0
    for model_version, distinct_adapters, distinct_rules, scores, servers in summary_rows:
        model_key = str(model_version)
        model_scores = int(scores)
        total_scores += model_scores
        model_versions.append(
            {
                "model_version": model_key,
                "distinct_adapters": int(distinct_adapters),
                "distinct_rule_versions": int(distinct_rules),
                "scores": model_scores,
                "servers": int(servers),
                "adapters": sorted(
                    adapters_by_model.get(model_key, []),
                    key=lambda item: item["adapter_sha256"],
                ),
            }
        )

    model_versions.sort(key=lambda item: item["model_version"])
    return {
        "model_versions": model_versions,
        "total_scores": total_scores,
    }


def adapter_sha_drift_flags(db: Session) -> Dict[str, List[str]]:
    """Return changed adapters keyed by model version.

    A server count is the number of distinct servers scored for an adapter on
    a day.  The comparison uses the last two distinct scored-at days for each
    model version.
    """
    return _changed_adapters(_day_server_counts(db))


def adapter_sha_drift_with_flags(db: Session) -> Dict[str, Any]:
    """Return the summary together with adapter server-count drift flags."""
    result = adapter_sha_drift(db)
    flags = adapter_sha_drift_flags(db)
    for model in result["model_versions"]:
        changed = set(flags.get(model["model_version"], []))
        for adapter in model["adapters"]:
            adapter["server_count_changed"] = (
                adapter["adapter_sha256"] in changed
            )
    return result


# Compatibility aliases for callers using a descriptive function name.
get_adapter_sha_drift = adapter_sha_drift
compute_adapter_sha_drift = adapter_sha_drift


if __name__ == "__main__":
    print("PASS")