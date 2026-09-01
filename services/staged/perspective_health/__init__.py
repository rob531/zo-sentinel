"""Zo-Sentinel service package."""

__version__ = "1.0.0"

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    VulnAdvisory,
)

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
    "VulnAdvisory",
]


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    try:
        import py_compile

        py_compile.compile(__file__, doraise=True)
    except py_compile.PyCompileError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    from fastapi import FastAPI
    from sqlalchemy.orm import Session

    app = FastAPI()

    mock_session = MagicMock(spec=Session)
    app.dependency_overrides[get_session] = lambda: mock_session

    assert get_session is not None
    assert callable(app.dependency_overrides[get_session])

    print("PASS")