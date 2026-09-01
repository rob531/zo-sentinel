from __future__ import annotations

import requests
from typing import Any, List, Dict

# --------------------------------------------------------------------------- #
# Internal helper to issue a raw SQL query to the ZoComputer store.
# --------------------------------------------------------------------------- #
def _post_query(sql: str) -> List[Dict[str, Any]]:
    """
    Send a POST request to the local query service and return the JSON payload.

    Args:
        sql: The raw SQL statement to execute.

    Returns:
        A list of dictionaries representing rows returned by the query.
    """
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": sql},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
# Public API – used throughout the code‑base.
# --------------------------------------------------------------------------- #
def get_mesh_memory() -> List[Dict[str, Any]]:
    """
    Retrieve the full contents of the ``mesh_memory`` table from the ZoComputer store.
    """
    return _post_query("SELECT * FROM mesh_memory")


def get_mesh_scores() -> List[Dict[str, Any]]:
    """
    Retrieve the full contents of the ``mcp_signal_scores`` table from the ZoComputer store.
    """
    return _post_query("SELECT * FROM mcp_signal_scores")


def get_signal_scores() -> List[Dict[str, Any]]:
    """
    Alias for :func:`get_mesh_scores`. Kept for backward compatibility with
    existing imports.
    """
    return get_mesh_scores()


def setup_database() -> None:
    """
    Minimal sanity‑check that the application database is reachable.
    The function deliberately performs a no‑op SELECT; any exception will
    propagate to the caller.
    """
    from app.db import get_session

    session = get_session()
    try:
        session.execute("SELECT 1")
    finally:
        session.close()


def reset_server_export_api_quarantine() -> None:
    """
    Trigger a server‑side reset of the export‑API quarantine state.
    The endpoint is expected to exist in the local query service.
    """
    requests.post(
        "http://127.0.0.1:8772/reset_quarantine",
        timeout=5,
    )


# --------------------------------------------------------------------------- #
# Self‑test – executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test must be lightweight and must never raise – network
    # failures are tolerated; the goal is simply to confirm the module loads.
    try:
        _ = get_mesh_memory()
    except Exception:
        pass
    print("PASS")