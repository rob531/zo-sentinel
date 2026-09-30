"""
services.staged.auth_strength_scoring_consumer.contract

FastAPI contract for the ``auth_strength_scoring_consumer`` staged service.
Provides a minimal health endpoint and a self‑test that runs with an
in‑memory SQLite database using ``StaticPool``.
"""

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# ----------------------------------------------------------------------
# Real data‑layer imports (must stay untouched for production)
# ----------------------------------------------------------------------
from app.db import get_session  # pragma: no cover
from app.models import (
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)  # pragma: no cover

# ----------------------------------------------------------------------
# Router / FastAPI app
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/health", tags=["health"])
def health_check() -> dict:
    """Simple health endpoint used by the self‑test."""
    return {"status": "ok"}


app = FastAPI(title="auth_strength_scoring_consumer")
app.include_router(router)

# ----------------------------------------------------------------------
# Self‑test (run with ``python -m services.staged.auth_strength_scoring_consumer.contract``)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create an in‑memory SQLite engine that mimics the real DB session.
    _engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # Dependency override that yields a SQLite session.
    def _override_get_session():
        db = _SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Apply the override to the FastAPI app.
    app.dependency_overrides[get_session] = _override_get_session

    # Run a tiny test client against the health endpoint.
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    assert response.json() == {"status": "ok"}, f"Unexpected payload: {response.json()}"
    print("PASS")