"""zo-sentinel package initializer.

Provides the FastAPI application instance and common dependencies used across
the service. The module is deliberately lightweight to avoid breaking existing
contracts.
"""

from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    VulnAdvisory,
)

# FastAPI application shared by the service.
app: FastAPI = FastAPI(title="Zo Sentinel")

@app.get("/health")
def health() -> dict[str, str]:
    """Simple health‑check endpoint."""
    return {"status": "ok"}

# Exported symbols for external imports.
__all__ = [
    "app",
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
    "VulnAdvisory",
]

# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)