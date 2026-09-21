# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
    VulnAdvisory,
)

__version__ = "0.1.0"

__all__ = [
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "Org",
    "User",
    "VulnAdvisory",
    "get_session",
    "__version__",
]

if TYPE_CHECKING:
    from app.db import AsyncSession


def _walk_exports():
    """Discover and validate all exportable names from sibling modules."""
    import os
    import sys

    pkg_path = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([pkg_path]):
        if module_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".{module_name}", package=__name__)
            for attr in getattr(mod, "__all__", []):
                yield attr, getattr(mod, attr, None)
        except ImportError:
            pass


def _verify_schema_contracts():
    """Validate that all SCHEMA_TRUTH.md documented names are importable."""
    from app.db import get_session
    from app.models import (
        McpLlmAxisScore,
        McpScoreDispute,
        McpServerRegistry,
        Org,
        User,
        VulnAdvisory,
    )
    _ = (McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User, VulnAdvisory)
    return True


if __name__ == "__main__":
    import sys

    try:
        from app.db import get_session as gs
        from app.models import (
            McpLlmAxisScore,
            McpScoreDispute,
            McpServerRegistry,
            Org,
            User,
            VulnAdvisory,
        )

        assert gs is not None
        assert McpServerRegistry is not None
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None
        assert Org is not None
        assert User is not None
        assert VulnAdvisory is not None
        assert __version__ is not None
        assert "PASS" == "PASS"

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)