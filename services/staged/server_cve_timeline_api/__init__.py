"""
zo-sentinel package initializer.

Provides the FastAPI application instance and re‑exports core data‑access
utilities so that intra‑service imports remain stable.
"""

from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# FastAPI application used by the service and by downstream modules.
app: FastAPI = FastAPI(title="Zo Sentinel Service")

# Simple health‑check endpoint – many services rely on a reachable root.
@app.get("/health")
def health() -> dict[str, str]:
    """Return a minimal health status."""
    return {"status": "ok"}

# Dependency shortcut for downstream modules.
def db_session() -> Depends:
    """Expose the session dependency directly."""
    return Depends(get_session)

# Exported names for `from zo_sentinel import *` usage.
__all__ = [
    "app",
    "get_session",
    "db_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
]

# --------------------------------------------------------------------
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
    else:
        sys.exit(1)