"""logic.py -- data/computation layer for the api_key_scope_audit_api service.

Provides a read‑only audit of API keys per organisation.  The function operates
directly on a SQLAlchemy Session obtained from ``app.db.get_session`` and
returns a plain‑dict matching the ``ScopeAuditResponse`` contract.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ApiKey, Org


def scope_audit(
    db: Session,
    *,
    limit: int = 100,
    org_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Return audit information about API keys.

    Args:
        db: SQLAlchemy session.
        limit: Maximum number of organisations to include in the ``orgs`` list.
        org_id: If supplied, restrict the result to a single organisation.

    Returns:
        A dict compatible with ``ScopeAuditResponse``:
        {
            "total_keys": int,
            "total_orgs_with_keys": int,
            "orphan_key_count": int,
            "orgs": [
                {
                    "org_id": int,
                    "org_name": str,
                    "key_count": int,
                    "labels": List[str],
                    "oldest_key_created_at": datetime,
                    "newest_key_created_at": datetime,
                    "unused_labels": List[str],
                },
                ...
            ],
        }
    """
    # ------------------------------------------------------------------ #
    # Total keys (including orphans)
    # ------------------------------------------------------------------ #
    total_keys: int = db.execute(select(func.count(ApiKey.id))).scalar_one()

    # ------------------------------------------------------------------ #
    # Orphan keys – keys whose org_id does not match any Org.id
    # ------------------------------------------------------------------ #
    orphan_subq = (
        select(ApiKey.id)
        .outerjoin(Org, ApiKey.org_id == Org.id)
        .where(Org.id.is_(None))
    )
    orphan_key_count: int = db.execute(orphan_subq).rowcount or 0

    # ------------------------------------------------------------------ #
    # Gather keys that belong to existing organisations
    # ------------------------------------------------------------------ #
    key_join_stmt = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.created_at,
            Org.id.label("org_id"),
            Org.name.label("org_name"),
        )
        .join(Org, ApiKey.org_id == Org.id)
    )
    if org_id is not None:
        key_join_stmt = key_join_stmt.where(Org.id == org_id)

    rows = db.execute(key_join_stmt).all()

    # Organise data per org
    org_data: Dict[int, Dict[str, Any]] = {}
    for (
        _key_id,
        _key_org_id,
        label,
        created_at,
        org_id_val,
        org_name,
    ) in rows:
        if org_id_val not in org_data:
            org_data[org_id_val] = {
                "org_id": org_id_val,
                "org_name": org_name,
                "key_count": 0,
                "labels": [],
                "oldest_key_created_at": created_at,
                "newest_key_created_at": created_at,
                "unused_labels": [],
            }
        entry = org_data[org_id_val]
        entry["key_count"] += 1
        entry["labels"].append(label)
        if created_at < entry["oldest_key_created_at"]:
            entry["oldest_key_created_at"] = created_at
        if created_at > entry["newest_key_created_at"]:
            entry["newest_key_created_at"] = created_at

    # Determine unused labels (labels that are empty strings)
    for entry in org_data.values():
        entry["unused_labels"] = [lbl for lbl in entry["labels"] if not lbl]

    # Apply limit
    orgs_list: List[Dict[str, Any]] = list(org_data.values())[:limit]

    result: Dict[str, Any] = {
        "total_keys": total_keys,
        "total_orgs_with_keys": len(org_data),
        "orphan_key_count": orphan_key_count,
        "orgs": orgs_list,
    }
    return result