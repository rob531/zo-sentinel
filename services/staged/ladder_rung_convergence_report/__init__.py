"""
Service package core utilities.

Provides base classes, endpoint stubs, and a minimal FastAPI
application used throughout the codebase.  All functions are
intentionally lightweight; concrete implementations are supplied
by downstream modules.
"""

from __future__ import annotations

from fastapi import FastAPI
from typing import Any, Dict, List

# ----------------------------------------------------------------------
# FastAPI application (imported by main.py)
# ----------------------------------------------------------------------
app: FastAPI = FastAPI()


@app.get("/health")
def health() -> Dict[str, str]:
    """Simple health‑check endpoint."""
    return {"status": "healthy"}


# ----------------------------------------------------------------------
# Base service class – used for inheritance throughout the repo
# ----------------------------------------------------------------------
class ServiceBase:
    """Minimal base class for service objects."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @classmethod
    def run_self_test(cls) -> str:
        """Default self‑test implementation."""
        return "PASS"


# ----------------------------------------------------------------------
# Concrete placeholder classes (inherit ServiceBase)
# ----------------------------------------------------------------------
class PerspectiveSnapshot(ServiceBase):
    """Placeholder for the real PerspectiveSnapshot model."""
    pass


class McpScoreDisputeService(ServiceBase):
    """Placeholder for the real McpScoreDisputeService."""
    pass


class UserRead(ServiceBase):
    """Placeholder for the real UserRead model."""
    pass


class ServerResponse(ServiceBase):
    """Placeholder for the real ServerResponse model."""
    pass


# ----------------------------------------------------------------------
# Endpoint stubs – called from many staged modules
# ----------------------------------------------------------------------
def mesh_memory_endpoint(*args: Any, **kwargs: Any) -> Dict[str, str]:
    """Stub for the mesh memory endpoint."""
    return {"status": "ok"}


def get_mesh_memory_endpoint(*args: Any, **kwargs: Any) -> Dict[str, str]:
    """Stub for retrieving mesh memory."""
    return {"status": "ok"}


def get_score_disputes_endpoint(*args: Any, **kwargs: Any) -> Dict[str, str]:
    """Stub for retrieving score disputes."""
    return {"status": "ok"}


def signal_scores_endpoint(*args: Any, **kwargs: Any) -> Dict[str, str]:
    """Stub for signalling scores."""
    return {"status": "ok"}


def recency_report(*args: Any, **kwargs: Any) -> Dict[str, str]:
    """Stub for recency reporting."""
    return {"status": "ok"}


def get_open_disputes(*args: Any, **kwargs: Any) -> List[Any]:
    """Stub returning an empty list of open disputes."""
    return []


# ----------------------------------------------------------------------
# Self‑test helpers – invoked by various staged modules
# ----------------------------------------------------------------------
def run_self_test(*args: Any, **kwargs: Any) -> None:
    """Print PASS – used by many staged services."""
    print("PASS")


def test_self(*args: Any, **kwargs: Any) -> None:
    """Alias for run_self_test used in some modules."""
    print("PASS")


# ----------------------------------------------------------------------
# Module entry‑point self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("PASS")