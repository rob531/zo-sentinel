"""logic.py -- score provenance data/computation layer.

Reads the authoritative application scoring table through the real SQLAlchemy
session and computes version groups, server/axis counts, and staleness.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _as_timestamp(value: Any) -> Optional[datetime]:
    """Convert supported database timestamp values to an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp_text(value: Any) -> Optional[str]:
    """Return a stable string representation for the response model."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_score_provenance(
    db: Session,
    server_id: Optional[str] = None,
    stale_after_days: Optional[int] = 7,
) -> Dict[str, Any]:
    """Return scoring provenance grouped by rule, model, and adapter.

    ``axis_count`` is the number of score rows in the group and
    ``server_count`` is the number of distinct servers represented by it.
    """
    if stale_after_days is None:
        stale_after_days = 7
    stale_after_days = int(stale_after_days)
    if stale_after_days < 1 or stale_after_days > 365:
        raise ValueError("stale_after_days must be between 1 and 365")

    query = select(
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.adapter_sha256,
        func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
        func.count(McpLlmAxisScore.id).label("axis_count"),
        func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
    )
    if server_id is not None:
        query = query.where(McpLlmAxisScore.server_id == server_id)

    query = query.group_by(
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.adapter_sha256,
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=stale_after_days)
    versions: List[Dict[str, Any]] = []

    for row in db.execute(query).all():
        last_scored = row.last_scored_at
        last_timestamp = _as_timestamp(last_scored)
        stale = bool(last_timestamp is not None and last_timestamp < cutoff)
        versions.append(
            {
                "decision_rule_version": row.decision_rule_version,
                "model_version": row.model_version,
                "adapter_sha256": row.adapter_sha256,
                "axis_count": int(row.axis_count or 0),
                "server_count": int(row.server_count or 0),
                "last_scored_at": _timestamp_text(last_scored),
                "stale": stale,
            }
        )

    versions.sort(
        key=lambda item: (
            item["decision_rule_version"] is None,
            item["decision_rule_version"] or "",
            item["model_version"] is None,
            item["model_version"] or "",
            item["adapter_sha256"] is None,
            item["adapter_sha256"] or "",
        )
    )

    return {
        "versions": versions,
        "stale_count": sum(1 for version in versions if version["stale"]),
        "total_versions": len(versions),
    }


def list_score_provenance(
    db: Session,
    server_id: Optional[str] = None,
    stale_after_days: Optional[int] = 7,
) -> Dict[str, Any]:
    """Alias for :func:`get_score_provenance`."""
    return get_score_provenance(
        db,
        server_id=server_id,
        stale_after_days=stale_after_days,
    )


def get_provenance(
    db: Session,
    server_id: Optional[str] = None,
    stale_after_days: Optional[int] = 7,
) -> Dict[str, Any]:
    """Alias for :func:`get_score_provenance`."""
    return get_score_provenance(
        db,
        server_id=server_id,
        stale_after_days=stale_after_days,
    )