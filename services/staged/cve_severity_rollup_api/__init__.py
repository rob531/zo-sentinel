"""Zo-sentinel service package."""

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    VulnAdvisory,
)

__all__ = [
    "get_session",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "VulnAdvisory",
]

if __name__ == "__main__":
    import sys

    try:
        from app.db import get_session as gs
        from app.models import (
            McpLlmAxisScore,
            McpScoreDispute,
            McpServerRegistry,
            VulnAdvisory,
        )

        assert gs is not None
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None
        assert McpServerRegistry is not None
        assert VulnAdvisory is not None
        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)