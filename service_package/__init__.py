# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from auto_emitted_service._impl import (
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
    _run_self_test,
)

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
]


if __name__ == "__main__":
    assert _run_self_test(), "Self-test failed"
    print("PASS")
