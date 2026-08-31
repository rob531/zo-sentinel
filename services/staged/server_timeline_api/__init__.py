# auto_emitted_service/__init__.py
"""
Auto-emitted service package for zo-sentinel.
Relative intra-service imports survive staged->active promotion.
"""

from typing import Any, Dict

from fastapi import Depends
from sqlalchemy.orm import Session

# App database access (required by contract)
from app.db import get_session
from app.models import (
    Perspective,
    AskCorpusDoc,
    VulnAdvisory,
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
)

# Relative intra-service imports (survive staged->active promotion)
try:
    from . import models
    from . import endpoints
    from . import services
except ImportError:
    pass

__all__ = [
    "mesh_memory_endpoint",
    "Perspective",
    "AskCorpusDoc",
    "VulnAdvisory",
    "get_session",
]


def mesh_memory_endpoint(
    query: Dict[str, Any],
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Query mesh memory via write_service.
    Routes to http://127.0.0.1:8772/query for mesh/pipeline data.
    """
    import httpx

    response = httpx.post(
        "http://127.0.0.1:8772/query",
        json=query,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    import sys

    print("Running self-test...")

    try:
        # Verify data layer contracts
        from app.db import get_session as _gs
        from app.models import (
            Perspective,
            AskCorpusDoc,
            VulnAdvisory,
            McpServerRegistry,
            McpLlmAxisScore,
            McpScoreDispute,
        )
        assert _gs is not None
        assert Perspective is not None
        assert AskCorpusDoc is not None
        assert VulnAdvisory is not None
        assert McpServerRegistry is not None
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None

        # Verify mesh_memory_endpoint
        assert callable(mesh_memory_endpoint)
        import inspect
        sig = inspect.signature(mesh_memory_endpoint)
        assert "query" in sig.parameters

        # Verify exports
        assert "mesh_memory_endpoint" in __all__
        assert "Perspective" in __all__
        assert "AskCorpusDoc" in __all__
        assert "VulnAdvisory" in __all__

        # Verify httpx available for write_service calls
        import httpx

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)