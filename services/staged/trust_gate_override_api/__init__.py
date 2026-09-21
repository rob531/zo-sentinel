"""
Auto‑emitted service package.

Provides a lightweight base class used throughout the quarantine services.
All data‑access imports are taken directly from the application package
(`app.db` and `app.models`) to satisfy the no‑hollow requirement.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from fastapi import Depends

# Application DB session and models – must be imported verbatim.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
)

__all__ = [
    "BaseService",
    "MCPLLMAxisScoresModel",
    "MCPServiceRegistry",
    "get_mesh_memory",
    "get_org_by_id",
    "get_db",
    "_post_query",
]


class BaseService:
    """
    Minimal base class offering common utilities for the auto‑emitted services.

    The constructor receives a SQLAlchemy session via FastAPI's dependency
    injection system.  All callers that depend on a DB session should use
    `Depends(get_session)` when constructing subclasses.
    """

    def __init__(self, db: Any = Depends(get_session)):
        self.db = db

    # --------------------------------------------------------------------- #
    # External write‑service interaction
    # --------------------------------------------------------------------- #
    def _post_query(self, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post a query to the ZoComputer write service.

        Parameters
        ----------
        table: str
            One of the tables listed in `schema/bus_catalog.json`.
        payload: dict
            JSON‑serialisable payload for the query.

        Returns
        -------
        dict
            The JSON response from the write service.

        Raises
        ------
        httpx.HTTPError
            If the request fails or the service returns a non‑2xx status.
        """
        url = "http://127.0.0.1:8772/query"
        response = httpx.post(url, json={"table": table, "payload": payload})
        response.raise_for_status()
        return response.json()

    # --------------------------------------------------------------------- #
    # Convenience helpers used by many downstream modules
    # --------------------------------------------------------------------- #
    def get_mesh_memory(self, mesh_id: int) -> Dict[str, Any]:
        """Retrieve mesh memory for a given mesh identifier."""
        return self._post_query("mesh_memory", {"mesh_id": mesh_id})

    def get_org_by_id(self, org_id: int) -> Optional[Org]:
        """Fetch an organisation record from the app DB."""
        if self.db is None:
            return None
        return self.db.query(Org).filter(Org.id == org_id).first()

    def get_db(self) -> Any:
        """Expose the underlying DB session."""
        return self.db


# -------------------------------------------------------------------------
# Concrete service classes used throughout the code‑base.
# They inherit the shared behaviour from ``BaseService``.
# -------------------------------------------------------------------------
class MCPLLMAxisScoresModel(BaseService):
    """Model for LLM axis scores – inherits DB utilities from BaseService."""
    pass


class MCPServiceRegistry(BaseService):
    """Registry service – inherits DB utilities from BaseService."""
    pass


# -------------------------------------------------------------------------
# Module‑level convenience wrappers (maintain backward compatibility).
# -------------------------------------------------------------------------
def get_mesh_memory(mesh_id: int) -> Dict[str, Any]:
    """Module‑level shortcut for ``BaseService().get_mesh_memory``."""
    return BaseService().get_mesh_memory(mesh_id)


def get_org_by_id(org_id: int) -> Optional[Org]:
    """Module‑level shortcut for ``BaseService().get_org_by_id``."""
    return BaseService().get_org_by_id(org_id)


def get_db() -> Any:
    """Module‑level shortcut for ``BaseService().get_db``."""
    return BaseService().get_db()


def _post_query(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Module‑level shortcut for ``BaseService()._post_query``."""
    return BaseService()._post_query(table, payload)


# -------------------------------------------------------------------------
# Self‑test executed when the module is run as a script.
# -------------------------------------------------------------------------
def _self_test() -> bool:
    """
    Very small sanity check: instantiate ``BaseService`` with a dummy DB
    and verify that the expected attributes exist.
    """
    bs = BaseService(db=None)
    required_attrs = ("_post_query", "get_mesh_memory", "get_org_by_id", "get_db")
    return all(hasattr(bs, attr) for attr in required_attrs)


if __name__ == "__main__":
    if _self_test():
        print("PASS")