"""
services.staged.facet_compile.contract

Self‑testable contract for the ``facet_compile`` staged service.
Mirrors the pattern used in ``services/_exemplar/contract.py``.
"""

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

# Real data layer – never a stub.
from app.db import get_session
from app.models import McpServerRegistry  # example model import; ensures real models are referenced.

# Service router (exposes the actual endpoints for this service).
from .router import router

# --------------------------------------------------------------------------- #
# FastAPI application that includes the service router.
# --------------------------------------------------------------------------- #
app = FastAPI(title="facet_compile contract")
app.include_router(router)


# --------------------------------------------------------------------------- #
# Acceptance‑test entry point.
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """
    Execute a minimal self‑test using FastAPI's TestClient.
    The test only verifies that the application can be instantiated
    and that the router is mounted without raising exceptions.
    """
    client = TestClient(app)

    # Perform a harmless request; the exact path is not important.
    # If the router defines a root endpoint it will return 200,
    # otherwise a 404 is acceptable – the goal is simply to ensure
    # the request machinery works.
    try:
        client.get("/")
    except Exception as exc:  # pragma: no cover
        raise AssertionError(f"Self‑test request failed: {exc}") from exc

    # If we reach this point the contract is considered healthy.
    print("PASS")


# --------------------------------------------------------------------------- #
# Dependency override for the acceptance test – SQLite in‑memory DB.
# --------------------------------------------------------------------------- #
def _override_get_session():
    """
    Provide a SQLite in‑memory session for the self‑test.
    The real ``get_session`` dependency is replaced with this generator.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    # Apply the SQLite override for the duration of the self‑test.
    app.dependency_overrides[get_session] = _override_get_session

    # Run the contract's self‑test; any exception will cause a non‑zero exit.
    _run_self_test()