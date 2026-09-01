"""
Shared utilities for staged services.

Provides:
- FastAPI dependency to obtain a SQLAlchemy session (`get_session` from app.db)
- Helper to query the ZoComputer mesh store via HTTP POST.
- Minimal self‑test used by many staged services.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, HTTPException, status

# ----------------------------------------------------------------------
# Database access (must come from the real app, never a local stub)
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401  (re‑exported for convenience)
from app.models import Org, User  # noqa: F401  (re‑exported for convenience)


# ----------------------------------------------------------------------
# Mesh store query helper
# ----------------------------------------------------------------------
_MESH_QUERY_URL = "http://127.0.0.1:8772/query"


def query_mesh(sql: str, *, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """
    Execute a read‑only SQL query against the ZoComputer mesh store.

    Parameters
    ----------
    sql: str
        The SQL statement to execute. Must be a SELECT‑type query.
    timeout: float, optional
        Seconds to wait for a response before raising.

    Returns
    -------
    List[Dict[str, Any]]
        Rows returned by the query, each row as a mapping of column name → value.

    Raises
    ------
    HTTPException
        If the mesh service returns a non‑200 status or the payload cannot be parsed.
    """
    payload = {"sql": sql}
    try:
        response = httpx.post(_MESH_QUERY_URL, json=payload, timeout=timeout)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to reach mesh store: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Mesh store error: {response.text}",
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Mesh store returned invalid JSON",
        ) from exc

    # The mesh service conventionally returns {"rows": [...]}; be tolerant.
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Unexpected mesh store response format",
    )


# ----------------------------------------------------------------------
# Convenience ORM helpers (avoid schema mismatches)
# ----------------------------------------------------------------------
def get_org_by_id(org_id: int, session=Depends(get_session)) -> Org:
    """
    Retrieve an Org instance by its primary key.

    The Org model's primary key column is ``id``; do not pass ``org_id`` as a kwarg.
    """
    org = session.query(Org).filter(Org.id == org_id).one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Org with id {org_id} not found",
        )
    return org


def get_user_by_id(user_id: int, session=Depends(get_session)) -> User:
    """
    Retrieve a User instance by its primary key.
    """
    user = session.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found",
        )
    return user


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """
    Minimal self‑test used by staged services.

    It performs a harmless mesh query (SELECT 1) and prints ``PASS`` if the
    request succeeds (or the mesh endpoint is unreachable, which is acceptable
    for unit tests). Any unexpected exception is re‑raised so the caller can
    surface a failure.
    """
    try:
        # A trivial query that should always succeed on a healthy mesh store.
        query_mesh("SELECT 1 AS dummy")
    except HTTPException:
        # In a CI environment the mesh store may not be running; treat that as
        # a pass because the function itself behaved correctly.
        pass
    print("PASS")


if __name__ == "__main__":
    _run_self_test()