"""
zo_sentinel package core exports.

Provides direct access to the application database session and the primary
SQLAlchemy models used throughout the codebase.  All imports that previously
relied on definitions inside this module now reference the canonical models
from ``app.models`` to avoid schema mismatches.
"""

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
]

# --------------------------------------------------------------------------- #
# Self‑test (executed when running ``python -m zo_sentinel``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Verify that the exported symbols exist and are the expected types.
    assert callable(get_session), "get_session must be callable"
    for model in (McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory):
        assert hasattr(model, "__tablename__"), f"{model.__name__} missing __tablename__"
    print("PASS")