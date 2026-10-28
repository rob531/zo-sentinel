from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import text

try:
    from app.db import SessionLocal
except ImportError:
    try:
        from app.db.session import SessionLocal
    except ImportError:
        from app.database import SessionLocal


KNOWN_STATUSES = ("open", "accepted", "rejected", "resolved")

_TIER_ALIGNMENT_SQL = text(
    """
    SELECT
        d.id AS dispute_id,
        d.server_id AS server_id,
        d.status AS status,
        d.proposed_overall_risk AS proposed_overall_risk,
        d.created_at AS created_at,
        d.resolved_at AS resolved_at,
        r.risk_tier AS current_risk_tier
    FROM mcp_score_disputes AS d
    LEFT JOIN mcp_server_registry AS r
        ON r.server_id = d.server_id
    ORDER BY d.created_at ASC, d.id ASC
    """
)


def _canonical_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.casefold()


def _normalize_status(value: Any) -> str:
    if value is None:
        return "other"
    normalized = str(value).strip().casefold()
    return normalized if normalized in KNOWN_STATUSES else "other"


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None
    return None


def _days_between(created_at: Any, resolved_at: Any, now: Optional[datetime] = None) -> int:
    created = _as_datetime(created_at)
    if created is None:
        return 0

    end = _as_datetime(resolved_at)
    if end is None:
        end = now or datetime.now(timezone.utc)

    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    seconds = (end - created).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 86400)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _build_payload(rows: Any) -> dict[str, Any]:
    by_status = {status: 0 for status in KNOWN_STATUSES}
    by_status["other"] = 0

    servers: list[dict[str, Any]] = []
    aligned_count = 0
    changed_count = 0
    unknown_count = 0

    now = datetime.now(timezone.utc)

    for index, row in enumerate(rows):
        server_id = _row_value(row, "server_id", 1)
        raw_status = _row_value(row, "status", 2)
        status = _normalize_status(raw_status)
        by_status[status] += 1

        proposed = _row_value(row, "proposed_overall_risk", 3)
        current = _row_value(row, "current_risk_tier", 6)
        created_at = _row_value(row, "created_at", 4)
        resolved_at = _row_value(row, "resolved_at", 5)

        proposed_tier = _canonical_tier(proposed)
        current_tier = _canonical_tier(current)

        if proposed_tier is None or current_tier is None:
            aligned = False
            unknown_count += 1
        elif proposed_tier == current_tier:
            aligned = True
            aligned_count += 1
        else:
            aligned = False
            changed_count += 1

        servers.append(
            {
                "server_id": server_id,
                "status": status,
                "proposed_overall_risk": proposed,
                "current_risk_tier": current,
                "aligned": aligned,
                "days_open": _days_between(created_at, resolved_at, now),
                "created_at": created_at,
                "resolved_at": resolved_at,
            }
        )

    total_disputes = len(servers)

    return {
        "total_disputes": total_disputes,
        "by_status": by_status,
        "alignment": {
            "aligned": aligned_count,
            "changed": changed_count,
            "unknown": unknown_count,
        },
        "servers": servers,
        "aligned_count": aligned_count,
        "changed_count": changed_count,
        "unknown_count": unknown_count,
    }


def get_tier_alignment(db: Any = None) -> dict[str, Any]:
    """Return dispute-to-current-tier alignment using the application database."""
    owns_session = db is None
    session = db if db is not None else SessionLocal()

    try:
        result = session.execute(_TIER_ALIGNMENT_SQL)
        if hasattr(result, "mappings"):
            rows = result.mappings().all()
        else:
            rows = list(result)
        return _build_payload(rows)
    finally:
        if owns_session:
            close = getattr(session, "close", None)
            if callable(close):
                close()


def get_dispute_tier_alignment(db: Any = None) -> dict[str, Any]:
    return get_tier_alignment(db)


def get_tier_alignment_data(db: Any = None) -> dict[str, Any]:
    return get_tier_alignment(db)


def compute_tier_alignment(db: Any = None) -> dict[str, Any]:
    return get_tier_alignment(db)


def tier_alignment(db: Any = None) -> dict[str, Any]:
    return get_tier_alignment(db)


if __name__ == "__main__":
    assert _normalize_status("OPEN") == "open"
    assert _normalize_status("unexpected") == "other"
    assert _canonical_tier(" High ") == "high"
    assert _canonical_tier(None) is None
    assert _days_between(
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 1, 3, tzinfo=timezone.utc),
    ) == 2
    print("PASS")