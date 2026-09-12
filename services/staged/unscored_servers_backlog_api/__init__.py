"""Zo-Sentinel service package."""

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

if __name__ == "__main__":
    from sqlalchemy import text

    session = next(get_session())
    session.execute(text("SELECT 1"))
    session.close()
    print("PASS")