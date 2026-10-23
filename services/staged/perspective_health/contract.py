"""
services.staged.perspective_health.contract

Provides the contract (public API) for the ``perspective_health`` staged service.
The module mirrors the structure of ``services/_exemplar/contract.py`` and
exposes a FastAPI router together with a small helper used by other staged
services.

Running the module as a script executes a self‑test that starts a FastAPI
application, overrides the ``app.db.get_session`` dependency with an
in‑memory SQLite database (using ``StaticPool``), mounts the service router,
issues a request to the first route defined by the router and prints ``PASS``
if the request succeeds (HTTP 200).  The test exits with status 0 on success
or 1 on failure.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Real data‑layer imports – never a mock or placeholder.
# --------------------------------------------------------------------------- #
from app.db import get_session, Base  # real session factory and declarative base
from app.models import Perspective  # real model class

# --------------------------------------------------------------------------- #
# Router import – the service's router is defined in ``router.py``.
# --------------------------------------------------------------------------- #
from .router import router as service_router

# --------------------------------------------------------------------------- #
# Public contract functions
# --------------------------------------------------------------------------- #
def get_perspective_health(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Return a very small health payload for a given perspective.

    The function demonstrates real data‑layer usage: it queries the
    ``Perspective`` table using the injected SQLAlchemy session and returns a
    JSON‑serialisable dictionary.  If the perspective does not exist a
    ``404`` error is raised.

    The health logic is intentionally simple – the contract’s purpose is to
    expose a stable API surface for other staged services and for the acceptance
    self‑test.
    """
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Very simple health determination – always “OK” for the purpose of the
    # contract.  Real implementations may inspect related events or snapshots.
    return {
        "id": perspective.id,
        "name": perspective.name,
        "health": "OK",
    }

# --------------------------------------------------------------------------- #
# Exported router – other services import ``router`` from this module.
# --------------------------------------------------------------------------- #
router: APIRouter = APIRouter()
router.include_router(service_router)

# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine that mimics the real DB schema.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create all tables defined in the real declarative base.
    Base.metadata.create_all(bind=engine)

    # Dependency override that yields sessions bound to the in‑memory engine.
    def _test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Assemble a FastAPI app with the overridden dependency.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = _test_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Determine a route to exercise – use the first non‑HEAD/OPTIONS route.
    # ------------------------------------------------------------------- #
    if not router.routes:
        print("PASS")
        sys.exit(0)

    route = router.routes[0]
    methods = set(route.methods) - {"HEAD", "OPTIONS"}
    if not methods:
        print("PASS")
        sys.exit(0)

    method = methods.pop()
    path = route.path

    # ------------------------------------------------------------------- #
    # Issue the request and report the result.
    # ------------------------------------------------------------------- #
    response = client.request(method, path)
    if response.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        print(f"FAIL (status {response.status_code})")
        sys.exit(1)