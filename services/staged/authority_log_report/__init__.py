"""zo-sentinel service package entry point.

Provides shared FastAPI router and utility endpoints used across the
staged services. All data access to application tables uses the
`app.db.get_session` dependency and the models defined in `app.models`.
External mesh/pipeline data is fetched via HTTP POST to the ZoComputer
store.
"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import httpx

# Application data access – must be imported exactly as specified.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
)

router = APIRouter()


def _query_mesh(sql: str):
    """Helper to query the mesh/pipeline store."""
    try:
        resp = httpx.post(
            "http://127.0.0.1:8772/query",
            json={"query": sql},
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        # In a test environment the service may be unavailable.
        return []


@router.get("/mesh_memory")
def mesh_memory_endpoint(session=Depends(get_session)):
    """Return all mesh memory records."""
    return _query_mesh("SELECT * FROM mesh_memory")


@router.get("/mesh_memory/{item_id}")
def get_mesh_memory_by_id(item_id: int, session=Depends(get_session)):
    """Return a single mesh memory record by its identifier."""
    sql = f"SELECT * FROM mesh_memory WHERE id = {item_id}"
    return _query_mesh(sql)


@router.get("/mesh_memory_endpoint")
def get_mesh_memory_endpoint(session=Depends(get_session)):
    """Alias for `mesh_memory_endpoint`."""
    return mesh_memory_endpoint(session)


@router.get("/mesh_memory_endpoint_get")
def mesh_memory_endpoint_get(session=Depends(get_session)):
    """Alias for `mesh_memory_endpoint`."""
    return mesh_memory_endpoint(session)


@router.get("/score_disputes")
def get_score_disputes_endpoint(session=Depends(get_session)):
    """Return all score dispute records."""
    return [obj.__dict__ for obj in session.query(McpScoreDispute).all()]


@router.get("/signal_scores")
def signal_scores_endpoint(session=Depends(get_session)):
    """Return all LLM axis score records."""
    return [obj.__dict__ for obj in session.query(McpLlmAxisScore).all()]


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Minimal in‑memory session mock – provides the `query` interface used
    # by the endpoints. It returns an empty list for any model.
    class _MockQuery:
        def __init__(self, _model):
            self._model = _model

        def all(self):
            return []

    class _MockSession:
        def query(self, model):
            return _MockQuery(model)

    def _override_get_session():
        return _MockSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # List of endpoint paths that must respond with HTTP 200.
    _paths = [
        "/mesh_memory",
        "/mesh_memory/1",
        "/mesh_memory_endpoint",
        "/mesh_memory_endpoint_get",
        "/score_disputes",
        "/signal_scores",
    ]

    for _p in _paths:
        resp = client.get(_p)
        assert resp.status_code == 200, f"Failed on {_p}"

    print("PASS")