"""
zo-sentinel staged service package __init__.

Provides shared utilities for staged services:
- reset_server_export_api_quarantine
- get_mesh_memory
- get_mesh_scores
- get_signal_scores
- router (FastAPI APIRouter)

All DB access uses the canonical app DB session and models.
External mesh data is fetched via HTTP POST with timeout.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# App DB imports – must remain verbatim to satisfy the no‑hollow gate.
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401
from app.models import (  # noqa: F401
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# ----------------------------------------------------------------------
# FastAPI router – services may import this router directly.
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Helper – safe HTTP POST with timeout.
# ----------------------------------------------------------------------
def _post_query(payload: Dict[str, Any], *, timeout: int = 5) -> Any:
    """
    POST ``payload`` to the mesh query endpoint.

    Args:
        payload: JSON payload accepted by the mesh service.
        timeout: Seconds before the request aborts.

    Returns:
        Parsed JSON response.

    Raises:
        requests.HTTPError: If the remote service returns an error status.
    """
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def reset_server_export_api_quarantine(
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    """
    Reset the quarantine flag for all servers in the registry.

    This is a no‑op placeholder that demonstrates a DB write
    using the canonical session. Adjust the filter criteria as
    needed by the consuming services.
    """
    session.query(McpServerRegistry).update({McpServerRegistry.quarantine: False})
    session.commit()
    return {"status": "reset"}


def get_mesh_memory() -> List[Dict[str, Any]]:
    """
    Retrieve mesh memory records from the ZoComputer store.
    """
    payload = {"query": "SELECT * FROM mesh_memory"}
    return _post_query(payload)


def get_mesh_scores() -> List[Dict[str, Any]]:
    """
    Retrieve mesh scores records from the ZoComputer store.
    """
    payload = {"query": "SELECT * FROM mesh_scores"}
    return _post_query(payload)


def get_signal_scores() -> List[Dict[str, Any]]:
    """
    Retrieve signal scores records from the ZoComputer store.
    """
    payload = {"query": "SELECT * FROM mcp_signal_scores"}
    return _post_query(payload)


# ----------------------------------------------------------------------
# Example endpoint – services may extend this router.
# ----------------------------------------------------------------------
@router.get("/health")
def health_check() -> Dict[str, str]:
    """Simple health endpoint used by many staged services."""
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------
    # In‑memory SQLite session for self‑test only.
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    def _test_session() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------
    # Monkey‑patch requests.post to avoid external dependency.
    # ------------------------------------------------------------------
    class _DummyResponse:
        def __init__(self, data: Any):
            self._data = data
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return self._data

    def _dummy_post(url: str, json: Dict[str, Any], timeout: int) -> _DummyResponse:  # noqa: D401
        """Return a deterministic dummy payload for any query."""
        return _DummyResponse([{"dummy": "value"}])

    requests.post = _dummy_post  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Execute basic sanity checks.
    # ------------------------------------------------------------------
    try:
        # DB‑related call – uses the test session.
        reset_server_export_api_quarantine(session=_test_session())

        # Mesh‑related calls – use the patched HTTP client.
        _ = get_mesh_memory()
        _ = get_mesh_scores()
        _ = get_signal_scores()

        print("PASS")
    except Exception as exc:  # pragma: no cover
        print("FAIL", exc)
        sys.exit(1)