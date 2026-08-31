"""Zo-Sentinel: Auto-emitted service package for MCP server lifecycle management."""

from app.models import (
    PerspectiveSnapshot,
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)
from app.db import get_session

__all__ = [
    "PerspectiveSnapshot",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
    "get_session",
]


class SentinelError(Exception):
    """Base exception for zo-sentinel operations."""
    pass


if __name__ == "__main__":
    # Self-test: verify package loads and exports are valid
    try:
        assert PerspectiveSnapshot is not None
        assert McpServerRegistry is not None
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None
        assert VulnAdvisory is not None
        assert get_session is not None
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        raise