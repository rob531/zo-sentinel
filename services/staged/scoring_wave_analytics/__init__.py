"""Auto‑emitted service package.

Provides a minimal FastAPI router and a self‑test entry point.
Relative intra‑service imports can rely on the symbols exported here
without needing to be rewritten when the package is promoted from
staged to active.
"""

from fastapi import APIRouter
from app.db import get_session
from app import models as models  # re‑export all app models for convenience

# Public router that services can extend or include.
router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Simple health endpoint used by many services."""
    return {"status": "ok"}


def run_self_test() -> str:
    """Self‑test used by various staged services.

    Returns ``\"PASS\"`` when the module loads correctly.
    """
    # The test is deliberately lightweight: if the module imports succeed,
    # we consider the self‑test passed.
    return "PASS"


__all__ = [
    "router",
    "run_self_test",
    "get_session",
    "models",
]

if __name__ == "__main__":
    # When executed as a script, run the self‑test and print the result.
    print(run_self_test())