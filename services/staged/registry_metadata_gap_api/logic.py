from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _now() -> datetime:
    return datetime.utcnow()


def _row_to_dict(row: McpServerRegistry) -> Dict[str, Any]:
    return {
        "server_id": row.server_id,
        "name": row.name,
        "registry_source": row.registry_source,
        "url": row.url,
        "description": row.description,
        "trust_score": row.trust_score,
        "verdict": row.verdict,
        "verdict_reasoning": row.verdict_reasoning,
        "confidence": row.confidence,
        "last_assessed": row.last_assessed,
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
        "last_scanned": row.last_scanned,
        "scan_count": row.scan_count,
        "risk_tier": row.risk_tier,
        "meta": row.meta,
    }


def get_registry_metadata_gaps(
    limit: int = 50,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Return registry metadata completeness statistics.

    The result identifies missing metadata fields, counts incomplete servers by
    registry source, and lists the servers with the greatest number of missing
    fields.
    """
    if session is None:
        session = get_session()

    rows = session.execute(
        select(McpServerRegistry)
    ).scalars().all()

    missing_fields = (
        "missing_description",
        "missing_url",
        "missing_trust_score",
        "missing_confidence",
        "missing_last_assessed",
        "missing_risk_tier",
        "missing_verdict_reasoning",
    )

    gaps = {field: 0 for field in missing_fields}
    by_source: Dict[str, Dict[str, int]] = {}
    offenders: List[Dict[str, Any]] = []

    for row in rows:
        values = _row_to_dict(row)
        missing = []

        if not values["description"]:
            missing.append("missing_description")
        if not values["url"]:
            missing.append("missing_url")
        if values["trust_score"] is None:
            missing.append("missing_trust_score")
        if values["confidence"] is None:
            missing.append("missing_confidence")
        if values["last_assessed"] is None:
            missing.append("missing_last_assessed")
        if not values["risk_tier"]:
            missing.append("missing_risk_tier")
        if not values["verdict_reasoning"]:
            missing.append("missing_verdict_reasoning")

        for field in missing:
            gaps[field] += 1

        source = values["registry_source"] or "unknown"
        source_stats = by_source.setdefault(source, {"total": 0, "incomplete": 0})
        source_stats["total"] += 1
        if missing:
            source_stats["incomplete"] += 1

        offenders.append(
            {
                "server_id": values["server_id"],
                "name": values["name"],
                "registry_source": values["registry_source"],
                "missing": missing,
            }
        )

    offenders.sort(
        key=lambda item: (-len(item["missing"]), item["server_id"] or "")
    )

    return {
        "total_servers": len(rows),
        "gaps": gaps,
        "by_source": [
            {
                "registry_source": source,
                "total": stats["total"],
                "incomplete": stats["incomplete"],
            }
            for source, stats in sorted(by_source.items())
        ],
        "worst_offenders": offenders[: max(0, limit)],
        "limit": limit,
    }


def get_registry_metadata_gap_summary(
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Return only the aggregate metadata gap summary."""
    result = get_registry_metadata_gaps(limit=0, session=session)
    return {
        "total_servers": result["total_servers"],
        "gaps": result["gaps"],
        "by_source": result["by_source"],
    }