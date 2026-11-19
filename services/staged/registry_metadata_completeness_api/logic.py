"""Logic for the registry metadata completeness API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


FIELDS = ("url", "description", "trust_score", "verdict", "risk_tier")


def _is_missing(field: str, value: Any) -> bool:
    if field == "trust_score":
        return value is None
    return value is None or value == ""


def _completeness(missing_by_field: Dict[str, int], total: int) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for field in FIELDS:
        missing = int(missing_by_field.get(field, 0))
        present = total - missing
        fields[field] = {
            "present": present,
            "missing": missing,
            "completeness_ratio": (float(present) / total) if total else 1.0,
        }
    return fields


def get_registry_metadata_completeness(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Return metadata completeness statistics for the MCP registry."""
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.url,
        McpServerRegistry.description,
        McpServerRegistry.trust_score,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        statement = statement.where(McpServerRegistry.registry_source == registry_source)

    rows = db.execute(statement).all()
    total = len(rows)
    overall_missing = {field: 0 for field in FIELDS}
    source_rows: Dict[str, list[tuple[Any, ...]]] = {}

    for row in rows:
        source = row.registry_source or "unknown"
        source_rows.setdefault(source, []).append(tuple(row))
        for field in FIELDS:
            if _is_missing(field, getattr(row, field)):
                overall_missing[field] += 1

    by_source: list[Dict[str, Any]] = []
    for source in sorted(source_rows):
        source_total = len(source_rows[source])
        source_missing = sum(
            1
            for row in source_rows[source]
            if any(_is_missing(field, getattr(row, field)) for field in FIELDS)
        )
        by_source.append(
            {
                "registry_source": source,
                "total": source_total,
                "missing": source_missing,
                "completeness_ratio": (
                    float(source_total - source_missing) / source_total
                    if source_total
                    else 1.0
                ),
            }
        )

    return {
        "overall": {
            "total": total,
            "fields": _completeness(overall_missing, total),
        },
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_metadata_completeness(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility alias for the registry completeness computation."""
    return get_registry_metadata_completeness(db, registry_source)


def compute_metadata_completeness(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility alias for the registry completeness computation."""
    return get_registry_metadata_completeness(db, registry_source)