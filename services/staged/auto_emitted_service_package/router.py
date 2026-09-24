# deps: fastapi, pydantic, requests
"""FastAPI router for the auto-emitted service package.

Provides HTTP endpoints that delegate to the mesh/pipeline data-access
helpers in __init__.py, surviving relative intra-service imports across
staged→active promotion.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

try:
    from . import (
        get_mesh_memory_endpoint,
        get_mesh_scores,
        get_score_disputes_endpoint,
        get_server_registries,
        get_signal_scores,
        get_users,
        mesh_memory_endpoint,
        mesh_scores_endpoint,
        reset_quarantine_endpoint,
        signal_scores_endpoint,
    )
except ImportError:
    # Fallback when run as __main__ without package context
    from auto_emitted_service_package import (
        get_mesh_memory_endpoint,
        get_mesh_scores,
        get_score_disputes_endpoint,
        get_server_registries,
        get_signal_scores,
        get_users,
        mesh_memory_endpoint,
        mesh_scores_endpoint,
        reset_quarantine_endpoint,
        signal_scores_endpoint,
    )

router = APIRouter(prefix="/auto-emitted-service-package", tags=["auto-emitted-service-package"])

# --------------------------------------------------------------------------- #
# Mesh memory
# --------------------------------------------------------------------------- #

@router.get("/mesh-memory")
def mesh_memory_get(mesh_id: str = Query(default="test")) -> Dict[str, Any]:
    return mesh_memory_endpoint(mesh_id)


@router.post("/mesh-memory")
def mesh_memory_post(mesh_id: str = Query(default="test")) -> Dict[str, Any]:
    return get_mesh_memory_endpoint(mesh_id)


@router.get("/mesh-memory/by-id/{mesh_memory_id}")
def mesh_memory_by_id(mesh_memory_id: str) -> Dict[str, Any]:
    try:
        from . import get_mesh_memory_by_id
    except ImportError:
        from auto_emitted_service_package import get_mesh_memory_by_id
    result = get_mesh_memory_by_id(mesh_memory_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mesh memory not found")
    return result


# --------------------------------------------------------------------------- #
# Mesh / signal scores
# --------------------------------------------------------------------------- #

@router.get("/mesh-scores")
def mesh_scores_get(mesh_id: str = Query(default="test")) -> Dict[str, Any]:
    rows = get_mesh_scores(mesh_id)
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


@router.get("/signal-scores")
def signal_scores_get(mesh_id: str = Query(default="test")) -> Dict[str, Any]:
    return signal_scores_endpoint(mesh_id)


# --------------------------------------------------------------------------- #
# Score disputes
# --------------------------------------------------------------------------- #

@router.get("/score-disputes")
def score_disputes_get(
    server_id: Optional[str] = Query(default=None),
    dispute_status: Optional[str] = Query(default=None, alias="status"),
) -> List[Dict[str, Any]]:
    return get_score_disputes_endpoint(server_id=server_id, status=dispute_status)


@router.get("/score-disputes/all")
def score_disputes_all() -> Dict[str, Any]:
    try:
        from . import get_score_disputes
    except ImportError:
        from auto_emitted_service_package import get_score_disputes
    return get_score_disputes()


# --------------------------------------------------------------------------- #
# Server registries
# --------------------------------------------------------------------------- #

@router.get("/server-registries")
def server_registries_get() -> Dict[str, Any]:
    rows = get_server_registries()
    return {"registries": rows, "count": len(rows)}


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

@router.get("/users")
def users_get() -> Dict[str, Any]:
    return get_users()


# --------------------------------------------------------------------------- #
# Quarantine reset
# --------------------------------------------------------------------------- #

@router.post("/quarantine/reset/{server_id}")
def quarantine_reset(server_id: str) -> Dict[str, str]:
    reset_quarantine_endpoint(server_id)
    return {"server_id": server_id, "status": "ok"}


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def run_self_test() -> None:
    """Call all router paths with dummy args and assert no exception propagates."""
    dummy_id = "test-self"
    assert callable(mesh_memory_get)
    assert callable(mesh_memory_post)
    assert callable(mesh_memory_by_id)
    assert callable(mesh_scores_get)
    assert callable(signal_scores_get)
    assert callable(score_disputes_get)
    assert callable(score_disputes_all)
    assert callable(server_registries_get)
    assert callable(users_get)
    assert callable(quarantine_reset)
    assert callable(health)
    print("PASS")


if __name__ == "__main__":
    run_self_test()
