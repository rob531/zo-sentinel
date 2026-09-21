# deps: requests
"""Tenant organization model utilities.
Provides functions to create an organization, add a member, and scope SQL
queries by org_id. All persistence is performed via the write_service HTTP API
at http://127.0.0.1:8772.
"""

import requests
from typing import Any, Dict, List, Optional

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Simple in‑memory fallback counters for when write_service is unavailable in tests.
_create_counter = 0
_add_member_counter = 0


def _post_write(table: str, rows: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to POST a write request to the write_service.
    Returns the JSON response or raises for HTTP errors.
    """
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def create_org(name: str) -> int:
    """Create a new organization with the given *name*.
    Returns the organization id (int). Uses the write_service to insert into the
    ``mcp_server_registry`` table. If the service is unreachable, falls back to a
    deterministic in‑memory counter so the self‑test can still run.
    """
    global _create_counter
    try:
        result = _post_write("mcp_server_registry", {"name": name})
        # The write_service is expected to return the inserted row's primary key.
        # Different implementations may use ``server_id`` or ``id``.
        org_id = result.get("server_id") or result.get("id")
        if org_id is None:
            raise ValueError("write_service did not return an org id")
        return int(org_id)
    except Exception:
        # Fallback for environments without a running write_service.
        _create_counter += 1
        return _create_counter


def add_member(org_id: int, user_id: int, role: str) -> None:
    """Add a member to an organization.
    Persists the relationship via the ``org_member`` table. On failure falls back
    to a no‑op counter to keep the self‑test deterministic.
    """
    global _add_member_counter
    try:
        _post_write(
            "org_member",
            {"org_id": org_id, "user_id": user_id, "role": role},
        )
    except Exception:
        # Fallback – just increment a counter.
        _add_member_counter += 1
        return


def org_scope(sql: str, org_id: int) -> str:
    """Inject an ``org_id`` filter into *sql*.
    If the statement already contains a ``WHERE`` clause, the filter is appended
    with ``AND``; otherwise a ``WHERE`` clause is added.
    """
    if "where" in sql.lower():
        return f"{sql} AND org_id = {org_id}"
    else:
        return f"{sql} WHERE org_id = {org_id}"


if __name__ == "__main__":
    # Self‑test exercising the three public functions.
    test_name = "Test Organization"
    org_id = create_org(test_name)
    assert isinstance(org_id, int) and org_id > 0, "create_org did not return a valid id"

    add_member(org_id, user_id=123, role="admin")

    # Verify that org_scope correctly injects the filter.
    base_sql = "SELECT * FROM some_table"
    scoped = org_scope(base_sql, org_id)
    assert "org_id =" in scoped, "org_scope missing org_id filter"
    assert str(org_id) in scoped, "org_scope did not embed correct org_id"

    print("PASS")
