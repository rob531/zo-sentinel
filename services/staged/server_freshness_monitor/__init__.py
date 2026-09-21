"""
Server freshness monitoring service.
"""

__all__ = []

# Auto-emitted service package
try:
    import zo_sentinel
except ImportError:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    import zo_sentinel

if __name__ == "__main__":
    import sys
    missing = [s for s in __all__ if not hasattr(zo_sentinel, s)]
    if missing:
        print(f"FAIL: {missing}")
        sys.exit(1)
    print("PASS")