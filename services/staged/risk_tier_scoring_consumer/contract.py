"""
services.staged.risk_tier_scoring_consumer.contract

FastAPI contract for the ``risk_tier_scoring_consumer`` service.
Provides a minimal health endpoint and exposes the real ``get_session``
dependency from ``app.db`` so that other services can import and use it.
The module can be executed directly to run a self‑test using an
in‑memory SQLite database.
"""

from fastapi import FastAPI, APIRouter, Depends
from sqlalchemy.orm import Session

# Real DB session dependency – must be imported exactly as used by the app.
from app.db import get_session  # noqa: F401

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_session)):
    """Simple health check – returns a static payload."""
    return {"status": "ok"}


# FastAPI application that includes the router.
app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.risk_tier_scoring_consumer.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a throw‑away SQLite in‑memory engine for the test.
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    _SessionLocal = sessionmaker(bind=_engine)

    # Dependency override that returns a fresh SQLite session.
    def _override_get_session() -> Session:  # pragma: no cover
        return _SessionLocal()

    # Apply the override.
    app.dependency_overrides[get_session] = _override_get_session

    # Run the test client against the health endpoint.
    _client = TestClient(app)
    _response = _client.get("/health")
    if _response.status_code == 200:
        print("PASS")
        exit(0)
    else:
        print("FAIL")
        exit(1)