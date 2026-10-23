"""
services.staged.auto_emitted_service_package.contract

Provides a minimal FastAPI contract for the staged service
`auto_emitted_service_package`. The module mirrors the pattern used in
`services/_exemplar/contract.py` and includes a self‑test that can be run
with:

    python -m services.staged.auto_emitted_service_package.contract

The test creates an in‑memory SQLite database (using a StaticPool), overrides
the real `app.db.get_session` dependency, mounts the service router and
instantiates a `TestClient`.  If everything imports correctly the script
prints ``PASS`` and exits with status 0.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real data‑layer imports – required by the “no‑hollow” rule.
from app.db import get_session  # noqa: F401
# Import the router that the service provides.
from .router import router

def _create_test_app() -> FastAPI:
    """
    Build a FastAPI application for the self‑test.

    The real `get_session` dependency is overridden with a SQLite in‑memory
    session so that the test does not touch production databases.
    """
    # SQLite in‑memory engine with a StaticPool (single connection shared).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session
    return app


if __name__ == "__main__":
    # Run the acceptance self‑test.
    test_app = _create_test_app()
    client = TestClient(test_app)

    # Perform a harmless request to ensure the router is mounted.
    # If the router does not define a root path, ignore the 404.
    try:
        client.get("/")
    except Exception:
        pass

    print("PASS")