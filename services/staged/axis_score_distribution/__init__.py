"""Auto-emitted sentinel service package.

Re-exports maintained for backwards compatibility during staged->active promotion.
"""

from __future__ import annotations

# Re-export from api module
from sentinel_service.api import (
    get_mcp_config,
    get_mcp_score,
    update_mcp_score,
    query_mesh_signals,
)

# Re-export from models module
from sentinel_service.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)

# Re-export from utils module
from sentinel_service.utils import (
    compute_sentinel_score,
    format_score_breakdown,
)

__version__ = "1.0.0"

__all__ = [
    "get_mcp_config",
    "get_mcp_score",
    "update_mcp_score",
    "query_mesh_signals",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
    "compute_sentinel_score",
    "format_score_breakdown",
]

if __name__ == "__main__":
    # Self-test validation
    import sys

    try:
        # Verify core imports work
        from sentinel_service.api import get_mcp_config, get_mcp_score
        from sentinel_service.models import McpServerRegistry, McpLlmAxisScore
        from sentinel_service.utils import compute_sentinel_score

        # Verify classes are accessible
        assert McpServerRegistry is not None
        assert McpLlmAxisScore is not None

        # Verify functions are callable
        assert callable(get_mcp_config)
        assert callable(get_mcp_score)
        assert callable(compute_sentinel_score)

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)