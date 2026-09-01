"""zo_sentinel - Sentinel service package."""

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)

__version__ = "1.0.0"

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
    "__version__",
]


if __name__ == "__main__":
    import sys

    try:
        from app.db import get_session
        from app.models import (
            McpServerRegistry,
            McpLlmAxisScore,
            McpScoreDispute,
            VulnAdvisory,
        )
    except ImportError as e:
        print(f"FAIL: import error - {e}")
        sys.exit(1)

    assert get_session is not None
    assert McpServerRegistry is not None
    assert McpLlmAxisScore is not None
    assert McpScoreDispute is not None
    assert VulnAdvisory is not None

    print("PASS")