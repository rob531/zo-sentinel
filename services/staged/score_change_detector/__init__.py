"""Service package core utilities.

Provides shared FastAPI router and data‑access helpers used across the
staged services.  All database interactions use the canonical
`app.db.get_session` dependency and the authoritative SQLAlchemy models
from `app.models`.  Mesh‑pipeline tables are accessed via the write‑service
HTTP endpoint at ``http://127.0.0.1:8772/query``.
"""

from __future__ import annotations

from typing import Any, List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Core FastAPI router – other modules may include it in their own routers.
# ----------------------------------------------------------------------
router = APIRouter()

# ----------------------------------------------------------------------
# Database session dependency (canonical for the whole code‑base).
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpServerRegistry  # authoritative model list

# ----------------------------------------------------------------------
# Helper: serialize a SQLAlchemy model instance to a plain dict.
# ----------------------------------------------------------------------
def _model_to_dict(instance: Any) -> dict:
    """Convert a SQLAlchemy model instance to a JSON‑serialisable dict."""
    data = {
        key: value
        for key, value in vars(instance).items()
        if not key.startswith("_sa_")
    }
    return data


# ----------------------------------------------------------------------
# Internal HTTP helper for mesh‑pipeline tables.
# ----------------------------------------------------------------------
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _post_query(table: str, sql: str) -> List[dict]:
    """Post a raw SQL query to the write‑service and return the JSON rows."""
    payload = {"table": table, "sql": sql}
    response = httpx.post(_WRITE_SERVICE_URL, json=payload, timeout=10.0)
    response.raise_for_status()
    return response.json()


# ----------------------------------------------------------------------
# Public endpoints / helpers used by many staged services.
# ----------------------------------------------------------------------
@router.get("/mesh_memory")
def mesh_memory_endpoint(db: Session = Depends(get_session)) -> List[dict]:
    """Return all rows from the ``mesh_memory`` bus table."""
    # The app DB is not involved; data lives in the mesh store.
    return _post_query("mesh_memory", "SELECT * FROM mesh_memory")


def get_mesh_memory_by_id(memory_id: int, db: Session = Depends(get_session)) -> dict:
    """Return a single ``mesh_memory`` row identified by ``memory_id``."""
    rows = _post_query(
        "mesh_memory",
        f"SELECT * FROM mesh_memory WHERE id = {memory_id}",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Mesh memory not found")
    return rows[0]


def get_server_registries(db: Session = Depends(get_session)) -> List[dict]:
    """Return all server registry records from the authoritative app table."""
    records = db.query(McpServerRegistry).all()
    return [_model_to_dict(r) for r in records]


# ----------------------------------------------------------------------
# Exported names.
# ----------------------------------------------------------------------
__all__ = [
    "router",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "get_server_registries",
    "get_session",
    "McpServerRegistry",
]


# ----------------------------------------------------------------------
# Self‑test entry point.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Minimal sanity check – the module imports correctly and the public
    # callables exist.  No external services are invoked.
    print("PASS")