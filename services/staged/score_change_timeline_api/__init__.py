# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from . import routes, models, services
from .routes import mesh_scores_endpoint, orgs_endpoint, signal_scores_endpoint, dummy_post_endpoint
from .services import (
    get_signal_scores,
    get_mesh_memory,
    _dummy_post,
    _signal_scores_http,
    _run_self_test
)

__all__ = [
    "routes",
    "models",
    "services",
    "mesh_scores_endpoint",
    "orgs_endpoint",
    "signal_scores_endpoint",
    "dummy_post_endpoint",
    "get_signal_scores",
    "get_mesh_memory",
    "_dummy_post",
    "_signal_scores_http",
    "_run_self_test"
]

def _run_self_test():
    """
    Internal self-test function that verifies module structure and imports.
    """
    import sys
    from unittest.mock import MagicMock, patch
    
    try:
        # Verify all exported components are importable and callable
        from .services import (
            get_signal_scores,
            get_mesh_memory,
            _dummy_post,
            _signal_scores_http
        )
        from .routes import (
            mesh_scores_endpoint,
            orgs_endpoint,
            signal_scores_endpoint,
            dummy_post_endpoint
        )
        
        # Verify these are callable
        assert callable(get_signal_scores)
        assert callable(get_mesh_memory)
        assert callable(_dummy_post)
        assert callable(_signal_scores_http)
        assert callable(mesh_scores_endpoint)
        assert callable(orgs_endpoint)
        assert callable(signal_scores_endpoint)
        assert callable(dummy_post_endpoint)
        
        # Verify the self-test is callable
        assert callable(_run_self_test)
        
        print("PASS")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
        return False

if __name__ == "__main__":
    _run_self_test()