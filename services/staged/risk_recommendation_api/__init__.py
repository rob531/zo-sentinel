"""
Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

__version__ = "1.0.0"

import sys


def run_self_test() -> int:
    """Run package self-tests."""
    try:
        # Verify package can be imported
        import importlib
        pkg_name = __name__.split('.')[0] if '.' in __name__ else __name__
        importlib.import_module(pkg_name)
        
        # Verify version is set
        assert hasattr(sys.modules[pkg_name], '__version__'), "Missing __version__"
        
        print("PASS")
        return 0
    except Exception as e:
        print(f"FAIL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run_self_test())