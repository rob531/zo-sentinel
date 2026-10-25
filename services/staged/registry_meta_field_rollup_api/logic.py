from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


META_ROLLUP_FIELDS = (
    "registry_source",
    "ecosystem",
    "transport",
    "publisher",
    "package_type",
    "stars",
    "forks",
    "updated_at",
    "license",
    "homepage",
)


def _as_meta_object(value: Any) -> Optional[Mapping[str, Any]]:
    """Return a JSON object for values returned by the database driver."""
    if isinstance(value, Mapping):
        return value

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(decoded, Mapping):
            return decoded

    return None


def _field_results(
    metas: Iterable[Any], total_servers: int, fields: Iterable[str] = META_ROLLUP_FIELDS
) -> List[Dict[str, Any]]:
    meta_objects = [obj for obj in (_as_meta_object(meta) for meta in metas) if obj is not None]
    results: List[Dict[str, Any]] = []

    for key in fields:
        present = sum(1 for meta in meta_objects if key in meta)
        missing = total_servers - present
        pct_present = round((present / total_servers) * 100, 2) if total_servers else 0.0

        results.append(
            {
                "key": key,
                "present": present,
                "missing": missing,
                "pct_present": pct_present,
            }
        )

    return results


def _registry_meta_rollup(session: Session) -> Dict[str, Any]:
    total_servers = int(
        session.execute(text("SELECT COUNT(*) FROM mcp_server_registry")).scalar() or 0
    )

    rows = session.execute(
        text(
            "SELECT meta "
            "FROM mcp_server_registry "
            "WHERE jsonb_typeof(meta) = 'object'"
        )
    ).scalars().all()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "fields": _field_results(rows, total_servers),
    }


def get_registry_meta_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return the registry metadata key rollup from the application database."""
    return _registry_meta_rollup(session)


# Keep the common service entry-point names available to routers and callers.
get_meta_rollup = get_registry_meta_rollup
get_rollup = get_registry_meta_rollup


if __name__ == "__main__":
    _test_metas = [
        {"ecosystem": "npm"},
        {"ecosystem": "pypi"},
        {},
    ]
    _test_fields = _field_results(_test_metas, 3)
    _ecosystem = next(field for field in _test_fields if field["key"] == "ecosystem")

    assert _ecosystem["present"] == 2
    assert _ecosystem["missing"] == 1
    assert abs(_ecosystem["pct_present"] - 66.67) <= 0.5
    assert all(
        field["present"] + field["missing"] == 3 for field in _test_fields
    )
    print("PASS")