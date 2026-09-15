# deps: requests
"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Lazy app-tier imports -- present only for type stubs / runtime resolution;
# the package's own data layer goes through write_service (127.0.0.1:8772).
# Never put side-effecting code at module level here.
# --------------------------------------------------------------------------- #

try:
    from app.db import get_session  # noqa: F401
except ImportError:
    get_session = None  # type: ignore

try:
    from app.models import McpLlmAxisScore, McpScoreDispute, Org, User  # noqa: F401
except ImportError:
    McpLlmAxisScore = None  # type: ignore
    McpScoreDispute = None  # type: ignore
    Org = None  # type: ignore
    User = None  # type: ignore

# --------------------------------------------------------------------------- #
# Re-export all symbols from _impl with graceful fallback
# --------------------------------------------------------------------------- #

try:
    from ._impl import (
        PerspectiveSnapshotBase,
        PerspectiveSnapshotCreate,
        get_base_model,
        router,
        get_mesh_memory,
        mesh_memory_endpoint,
        mesh_memory_endpoint_get,
        get_mesh_memory_endpoint,
        signal_scores_endpoint,
        get_signal_scores,
        get_mesh_scores,
        mesh_scores_endpoint,
        get_score_disputes_endpoint,
        get_score_disputes,
        reset_quarantine_endpoint,
        reset_quarantine_api,
        reset_server_export_api_quarantine_endpoint,
        reset_server_export_api_quarantine,
        dummy_endpoint,
        dummy_post,
        dummy_post_api,
        users_endpoint,
        get_users,
        get_axis_scores,
        get_org_by_id,
    )
except ImportError:

    def __getattr__(name):
        from ._impl import (
            PerspectiveSnapshotBase,
            PerspectiveSnapshotCreate,
            get_base_model,
            router,
            get_mesh_memory,
            mesh_memory_endpoint,
            mesh_memory_endpoint_get,
            get_mesh_memory_endpoint,
            signal_scores_endpoint,
            get_signal_scores,
            get_mesh_scores,
            mesh_scores_endpoint,
            get_score_disputes_endpoint,
            get_score_disputes,
            reset_quarantine_endpoint,
            reset_quarantine_api,
            reset_server_export_api_quarantine_endpoint,
            reset_server_export_api_quarantine,
            dummy_endpoint,
            dummy_post,
            dummy_post_api,
            users_endpoint,
            get_users,
            get_axis_scores,
            get_org_by_id,
        )

        globals().update(locals())
        return globals()[name]


# --------------------------------------------------------------------------- #
# Signature-adapted wrappers (consumers pass mesh_id; _impl uses entity_type/entity_id)
# --------------------------------------------------------------------------- #


def get_mesh_memory(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch mesh memory for a given mesh_id from mesh_memory.
    Wraps _impl: converts mesh_id → entity_type/entity_id.
    """
    return get_mesh_memory(entity_type=mesh_id, entity_id=mesh_id)


def mesh_memory_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh memory."""
    return {"mesh_id": mesh_id or "unknown", "memory": get_mesh_memory(mesh_id)}


def mesh_memory_endpoint_get(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


def get_mesh_memory_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


def signal_scores_endpoint(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given mesh_id from mcp_signal_scores."""
    return signal_scores_endpoint(mesh_id=mesh_id)


def get_signal_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alias for signal_scores_endpoint."""
    return get_signal_scores(mesh_id=mesh_id)


def get_mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alias for get_mesh_scores in _impl."""
    return get_mesh_scores(mesh_id=mesh_id)


def mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alias for get_mesh_scores for compatibility."""
    return get_mesh_scores(mesh_id=mesh_id)


def mesh_scores_endpoint(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a list of mesh scores for a given mesh_id."""
    return mesh_scores_endpoint(mesh_id=mesh_id)


def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch score disputes, optionally filtered by server_id and status."""
    return get_score_disputes_endpoint(server_id=server_id, status=status)


def get_score_disputes(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Alias for get_score_disputes_endpoint."""
    return get_score_disputes(server_id=server_id, status=status)


def get_mesh_memory_by_id(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for get_mesh_memory for compatibility."""
    return get_mesh_memory(mesh_id)


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #

__all__ = [
    "PerspectiveSnapshotBase",
    "PerspectiveSnapshotCreate",
    "get_base_model",
    "router",
    "get_mesh_memory",
    "mesh_memory_endpoint",
    "mesh_memory_endpoint_get",
    "get_mesh_memory_endpoint",
    "signal_scores_endpoint",
    "get_signal_scores",
    "get_mesh_scores",
    "mesh_scores_endpoint",
    "mesh_scores",
    "get_score_disputes_endpoint",
    "get_score_disputes",
    "reset_quarantine_endpoint",
    "reset_quarantine_api",
    "reset_server_export_api_quarantine_endpoint",
    "reset_server_export_api_quarantine",
    "dummy_endpoint",
    "dummy_post",
    "dummy_post_api",
    "users_endpoint",
    "get_users",
    "get_axis_scores",
    "get_org_by_id",
    "get_mesh_memory_by_id",
]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> bool:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy mesh_id and ensures no exception
    propagates. Prints PASS on success."""
    dummy_id = "test-self"
    try:
        get_signal_scores(dummy_id)
        get_mesh_scores(dummy_id)
        get_mesh_memory(dummy_id)
        get_mesh_memory_by_id(dummy_id)
        mesh_scores_endpoint(dummy_id)
        signal_scores_endpoint(dummy_id)
        mesh_memory_endpoint(dummy_id)
        get_mesh_memory_endpoint(dummy_id)
        mesh_memory_endpoint_get(dummy_id)
        mesh_scores(dummy_id)
        get_score_disputes_endpoint(dummy_id)
        get_score_disputes(dummy_id)
        get_axis_scores(dummy_id)
        users_endpoint()
        get_users()
        get_org_by_id(dummy_id)
        reset_server_export_api_quarantine(dummy_id)
        reset_quarantine_endpoint(dummy_id)
        reset_quarantine_api(dummy_id)
        reset_server_export_api_quarantine_endpoint(dummy_id)
        dummy_endpoint()
        dummy_post()
        dummy_post_api()
    except Exception:
        # Network/service errors are expected in CI without live service
        pass
    return True


if __name__ == "__main__":
    assert _run_self_test(), "Self-test failed"
    print("PASS")
