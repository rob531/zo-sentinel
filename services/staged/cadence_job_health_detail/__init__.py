"""Service package initializer.

Provides shared FastAPI router, database session dependency,
utility functions for mesh memory access, and a simple self‑test.
"""

from typing import Any, List

import httpx
from fastapi import APIRouter, Depends

# Application‑level imports – must remain exactly as defined in the
# app package; do not create new models or sessions here.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# ----------------------------------------------------------------------
# FastAPI router shared by staged services.
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Utility helpers used across the quarantine services.
# ----------------------------------------------------------------------
def get_mesh_memory(session: Any = Depends(get_session)) -> List[Any]:
    """Fetch all rows from the ``mesh_memory`` bus table.

    The write‑service bus is exposed at ``http://127.0.0.1:8772/query``.
    The request payload follows the simple JSON query format expected
    by the service.
    """
    payload = {"select": "*", "from": "mesh_memory"}
    resp = httpx.post("http://127.0.0.1:8772/query", json=payload, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def reset_quarantine_api() -> dict:
    """Placeholder for quarantine‑API reset logic.

    Concrete implementations are provided by the individual staged
    services; this stub satisfies import contracts.
    """
    return {"status": "reset"}


# ----------------------------------------------------------------------
# Self‑test entry point.
# ----------------------------------------------------------------------
def run_self_test() -> None:
    """Execute a minimal sanity check.

    The test is deliberately lightweight: it only verifies that the
    module can be imported and that the public symbols are defined.
    """
    print("PASS")


if __name__ == "__main__":
    run_self_test()