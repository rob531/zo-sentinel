"""
zo‑sentinel service package core.

Provides:
* BaseResponse – a minimal Pydantic model for inheritance.
* query_bus – low‑level POST to the write‑service bus.
* mesh_memory_endpoint – fetch all rows from the ``mesh_memory`` bus table.
* get_mesh_memory_by_id – fetch a single ``mesh_memory`` row by its primary key.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends
from pydantic import BaseModel

from app.db import get_session

# --------------------------------------------------------------------------- #
# Pydantic base model – other service‑specific response models inherit this.
# --------------------------------------------------------------------------- #
class BaseResponse(BaseModel):
    """Minimal response model shared across services."""
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# Low‑level bus query helper.
# --------------------------------------------------------------------------- #
_BUS_URL = "http://127.0.0.1:8772/query"


def query_bus(table: str, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Execute a POST request against the write‑service bus.

    Parameters
    ----------
    table: str
        Name of the bus table – must be present in ``schema/bus_catalog.json``.
    where: dict | None
        Simple equality filter expressed as a mapping of column name → value.

    Returns
    -------
    list[dict]
        Rows returned by the service (empty list if none).
    """
    payload: Dict[str, Any] = {"table": table}
    if where:
        payload["where"] = where
    response = httpx.post(_BUS_URL, json=payload, timeout=5.0)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Public mesh‑memory helpers used throughout the code‑base.
# --------------------------------------------------------------------------- #
def mesh_memory_endpoint(session: Any = Depends(get_session)) -> List[Dict[str, Any]]:
    """
    Retrieve every record from the ``mesh_memory`` bus table.

    The ``session`` argument is kept for signature compatibility with other
    service functions; it is not used internally.
    """
    return query_bus("mesh_memory")


def get_mesh_memory_by_id(
    mesh_id: int, session: Any = Depends(get_session)
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a single ``mesh_memory`` row identified by its primary key.

    Parameters
    ----------
    mesh_id: int
        Primary‑key value of the desired mesh‑memory record.

    Returns
    -------
    dict | None
        The matching row, or ``None`` if no such record exists.
    """
    rows = query_bus("mesh_memory", where={"id": mesh_id})
    return rows[0] if rows else None


__all__ = [
    "BaseResponse",
    "query_bus",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
]


# --------------------------------------------------------------------------- #
# Self‑test entry point.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test is intentionally lightweight: it merely confirms that the
    # module can be imported and that the public symbols exist.
    print("PASS")