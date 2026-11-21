"""logic.py -- API-key inventory data and computation layer."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


_KEY_ID_LENGTH = 16


def _surrogate_key_id(value: Any) -> str:
    """Return a non-reversible, truncated surrogate for a database id."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:_KEY_ID_LENGTH]


def _age_days(created_at: datetime, now: Optional[datetime] = None) -> int:
    """Return the age of a key in whole days."""
    if now is None:
        now = datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    return max(0, int((now - created_at).total_seconds() // 86400))


def _is_stale(age_days: int, stale_days: int) -> bool:
    """Determine whether a key is older than the configured threshold."""
    return age_days > stale_days


def get_api_key_inventory(
    db: Session,
    org_id: Optional[str] = None,
    stale_days: int = 90,
) -> list[dict[str, Any]]:
    """Read API-key metadata and return sanitized inventory records."""
    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.created_at,
            Org.name,
        )
        .select_from(ApiKey)
        .outerjoin(Org, Org.id == ApiKey.org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    )

    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    now = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []

    for raw_id, key_org_id, label, created_at, org_name in db.execute(statement).all():
        age_days = _age_days(created_at, now)
        records.append(
            {
                "key_id": _surrogate_key_id(raw_id),
                "org_id": key_org_id,
                "org_name": org_name,
                "label": label,
                "created_at": created_at,
                "age_days": age_days,
                "stale": _is_stale(age_days, stale_days),
            }
        )

    return records


def list_api_keys(
    db: Session,
    org_id: Optional[str] = None,
    stale_days: int = 90,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers using the list operation name."""
    return get_api_key_inventory(db, org_id=org_id, stale_days=stale_days)


def get_api_keys(
    db: Session,
    org_id: Optional[str] = None,
    stale_days: int = 90,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers using the get operation name."""
    return get_api_key_inventory(db, org_id=org_id, stale_days=stale_days)


if __name__ == "__main__":
    _test_now = datetime.now(timezone.utc)
    _test_created = _test_now.replace(microsecond=0) - __import__("datetime").timedelta(days=200)
    assert _age_days(_test_created, _test_now) == 200
    assert _is_stale(200, 90)
    assert _is_stale(91, 90)
    assert not _is_stale(90, 90)
    assert _surrogate_key_id("seeded-id") != "seeded-id"
    assert "seeded-id" not in _surrogate_key_id("seeded-id")
    print("PASS")