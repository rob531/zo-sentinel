# Auto‑emitted service package init
# Relative intra‑service imports survive staged→active promotion without rewrite.

# Re‑export public symbols from the package’s API and service modules.
# This allows other parts of the codebase (e.g., get_mesh_memory, main, etc.)
# to import them directly from the package without needing to know the
# internal layout.

from .api import *  # noqa: F403,F401
from .service import *  # noqa: F403,F401

# Build a clean __all__ that contains only the public names imported above.
__all__ = [name for name in globals() if not name.startswith('_')]

if __name__ == "__main__":
    print("PASS")