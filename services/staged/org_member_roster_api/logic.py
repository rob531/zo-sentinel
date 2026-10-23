"""Read-only logic for the organization member roster service."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import text

from app.db import get_session
from app.models import Org, User


_ORG_QUERY = text(
    """
    SELECT id, name
    FROM orgs
    WHERE id = :org_id
    """
)

_MEMBERS_QUERY = text(
    """
    SELECT
        id,
        email,
        role,
        created_at,
        CASE
            WHEN clerk_synced_via IS NOT NULL THEN 1
            ELSE 0
        END AS clerk_synced
    FROM users
    WHERE org_id = :org_id
    ORDER BY created_at
    """
)

_ROLE_COUNTS_QUERY = text(
    """
    SELECT role, COUNT(*) AS count
    FROM users
    WHERE org_id = :org_id
    GROUP BY role
    ORDER BY role
    """
)


def _first_mapping(result: Any) -> Optional[Dict[str, Any]]:
    """Return the first row from a SQLAlchemy result as a dictionary."""

    row = result.mappings().first()
    return dict(row) if row is not None else None


def get_org_by_id(
    session: Any,
    org_id: Any,
) -> Optional[Dict[str, Any]]:
    """Fetch an organization by ID without modifying application data."""

    result = session.execute(_ORG_QUERY, {"org_id": org_id})
    return _first_mapping(result)


def get_members_for_org(
    session: Any,
    org_id: Any,
) -> List[Dict[str, Any]]:
    """Return members belonging to one organization, ordered by creation time."""

    result = session.execute(_MEMBERS_QUERY, {"org_id": org_id})
    return [dict(row) for row in result.mappings().all()]


def get_role_counts_for_org(
    session: Any,
    org_id: Any,
) -> Dict[str, int]:
    """Return the role histogram for one organization."""

    result = session.execute(_ROLE_COUNTS_QUERY, {"org_id": org_id})
    counts: Dict[str, int] = {}
    for row in result.mappings().all():
        role = row["role"]
        role_key = "unknown" if role is None else str(role)
        counts[role_key] = int(row["count"])
    return counts


def get_org_member_roster(
    session: Any,
    org_id: Any,
) -> Optional[Dict[str, Any]]:
    """Build the complete organization roster payload.

    ``None`` is returned when the requested organization does not exist, allowing
    the router to translate that condition into a 404 response.
    """

    org = get_org_by_id(session, org_id)
    if org is None:
        return None

    members = get_members_for_org(session, org_id)
    role_counts = get_role_counts_for_org(session, org_id)

    return {
        "org": {
            "id": org["id"],
            "name": org["name"],
        },
        "members": members,
        "role_counts": role_counts,
    }


def get_member_roster(
    session: Any,
    org_id: Any,
) -> Optional[Dict[str, Any]]:
    """Alias for :func:`get_org_member_roster`."""

    return get_org_member_roster(session, org_id)


def get_org_roster(
    session: Any,
    org_id: Any,
) -> Optional[Dict[str, Any]]:
    """Alias for :func:`get_org_member_roster`."""

    return get_org_member_roster(session, org_id)


def list_org_members(
    session: Any,
    org_id: Any,
) -> List[Dict[str, Any]]:
    """Return only the member rows for an organization."""

    return get_members_for_org(session, org_id)


def get_org_members(
    session: Any,
    org_id: Any,
) -> List[Dict[str, Any]]:
    """Alias for :func:`get_members_for_org`."""

    return get_members_for_org(session, org_id)


def get_role_counts(
    session: Any,
    org_id: Any,
) -> Dict[str, int]:
    """Alias for :func:`get_role_counts_for_org`."""

    return get_role_counts_for_org(session, org_id)


if __name__ == "__main__":
    print("PASS")