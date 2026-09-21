"""zo-sentinel staged service package.

Provides shared FastAPI endpoints and utility functions used across
staged services.  All data access is performed via the application
SQLAlchemy session imported from ``app.db`` and the models defined in
``app.models``.
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Application‑level data access (must be used, not re‑implemented)
from app.db import get_session
from app.models import Base, MeshMemory, MeshScore, SignalScore  # type: ignore

app = FastAPI()


def _serialize(obj):
    """Convert a SQLAlchemy model instance to a plain dict."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ----------------------------------------------------------------------
# Public utility functions – called directly by other staged services
# ----------------------------------------------------------------------
def get_mesh_memory():
    """Return all rows from the ``mesh_memory`` table."""
    with get_session() as session:
        rows = session.query(MeshMemory).all()
        return [_serialize(r) for r in rows]


def get_mesh_scores():
    """Return all rows from the ``mesh_scores`` table."""
    with get_session() as session:
        rows = session.query(MeshScore).all()
        return [_serialize(r) for r in rows]


def get_signal_scores():
    """Return all rows from the ``signal_scores`` table."""
    with get_session() as session:
        rows = session.query(SignalScore).all()
        return [_serialize(r) for r in rows]


def reset_server_export_api_quarantine():
    """
    Placeholder for a routine that would reset quarantine state.
    Implementations in staged services may extend this.
    """
    # No‑op – real logic lives in the calling service.
    return {"status": "reset"}


# ----------------------------------------------------------------------
# FastAPI endpoints – used by the self‑test and by external callers
# ----------------------------------------------------------------------
@app.get("/mesh_memory")
def mesh_memory_endpoint(session=Depends(get_session)):
    rows = session.query(MeshMemory).all()
    return [_serialize(r) for r in rows]


@app.get("/mesh_scores")
def mesh_scores_endpoint(session=Depends(get_session)):
    rows = session.query(MeshScore).all()
    return [_serialize(r) for r in rows]


@app.get("/signal_scores")
def signal_scores_endpoint(session=Depends(get_session)):
    rows = session.query(SignalScore).all()
    return [_serialize(r) for r in rows]


@app.post("/dummy")
def dummy_post_endpoint(payload: dict = {}):
    """A trivial POST endpoint used by several staged services."""
    return _dummy_post(payload)


def _dummy_post(payload: dict):
    """Internal helper for ``dummy_post_endpoint``."""
    return {"received": payload}


# ----------------------------------------------------------------------
# Entry point for running the service directly
# ----------------------------------------------------------------------
def main():
    """Run the FastAPI app with Uvicorn."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


# ----------------------------------------------------------------------
# Self‑test executed when the module is run as a script
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create an in‑memory SQLite DB and bind the app models to it.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def test_session():
        return TestSession()

    # Override the production dependency with the test session.
    app.dependency_overrides[get_session] = test_session

    client = TestClient(app)

    # Minimal sanity checks – all endpoints must return 200.
    checks = [
        client.get("/mesh_memory"),
        client.get("/mesh_scores"),
        client.get("/signal_scores"),
        client.post("/dummy", json={"foo": "bar"}),
    ]

    if all(r.status_code == 200 for r in checks):
        print("PASS")
    else:
        print("FAIL")