# services/staged/app_router_registry_api/contract.py
"""
Contract module for the ``app_router_registry_api`` service.

Provides a ``/api/routers`` endpoint that enumerates the FastAPI routers
mounted in the current application instance.  The endpoint returns a JSON
object with a ``routers`` key containing a list of router descriptors.

The module can be executed directly to run a self‑test that starts a minimal
FastAPI app, mounts a couple of mock routers and verifies that the endpoint
behaves as expected.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Real data‑layer imports – required for production usage.
# --------------------------------------------------------------------------- #
from app.db import Base, get_session  # noqa: F401  (Base needed for test DB)
from app.models import McpServerRegistry  # noqa: F401  (used in health lookup)

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class RouterInfo(BaseModel):
    """Descriptor for a mounted FastAPI router."""
    name: str
    prefix: str
    tags: List[str] = []
    methods: List[str] = []
    health_status: Optional[str] = None
    last_heartbeat: Optional[datetime] = None


class RoutersResponse(BaseModel):
    routers: List[RouterInfo]


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get(
    "/api/routers",
    response_model=RoutersResponse,
    summary="List all mounted routers",
    tags=["router-registry"],
)
async def list_routers(request: Request, db: Session = Depends(get_session)):
    """
    Inspect the current FastAPI application (available via ``request.app``)
    and return a summary of each mounted ``APIRoute``.  Health information
    is looked up in ``McpServerRegistry`` when a matching entry exists.
    """
    app: FastAPI = request.app
    router_infos: List[RouterInfo] = []

    for route in app.routes:
        # Only consider APIRoute instances (ignore static files, etc.)
        if hasattr(route, "methods") and hasattr(route, "path"):
            name = getattr(route, "name", "")
            prefix = getattr(route, "path", "")
            tags = getattr(route, "tags", [])
            methods = sorted(list(getattr(route, "methods", [])))

            # Attempt to fetch health status from the server registry.
            health_status: Optional[str] = None
            last_heartbeat: Optional[datetime] = None
            if name:
                reg = (
                    db.query(McpServerRegistry)
                    .filter(McpServerRegistry.name == name)
                    .first()
                )
                if reg is not None:
                    # ``last_seen`` is the closest analogue to a heartbeat.
                    health_status = "registered"
                    last_heartbeat = getattr(reg, "last_seen", None)

            router_infos.append(
                RouterInfo(
                    name=name,
                    prefix=prefix,
                    tags=tags,
                    methods=methods,
                    health_status=health_status,
                    last_heartbeat=last_heartbeat,
                )
            )

    return RoutersResponse(routers=router_infos)


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    """
    Run a minimal self‑test:

    1. Create an in‑memory SQLite DB and bind ``get_session`` to it.
    2. Create the required tables (only ``McpServerRegistry`` is needed).
    3. Build a FastAPI app, mount this router and two dummy routers.
    4. Issue a request to ``/api/routers`` and verify the response.
    5. Print ``PASS`` and exit with status 0 on success.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # 1. In‑memory SQLite engine
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------- #
    # 2. Create tables required for the test
    # ------------------------------------------------------------------- #
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # 3. Dependency override for ``get_session``
    # ------------------------------------------------------------------- #
    def _override_get_session() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # 4. Build the test FastAPI app
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = _override_get_session
    test_app.include_router(router)  # include the contract router

    # Two mock routers --------------------------------------------------- #
    mock_router_a = APIRouter()
    mock_router_b = APIRouter()

    @mock_router_a.get("/hello")
    async def hello_a() -> str:
        return "a"

    @mock_router_b.get("/world")
    async def hello_b() -> str:
        return "b"

    test_app.include_router(mock_router_a, prefix="/mock1", tags=["mock"])
    test_app.include_router(mock_router_b, prefix="/mock2", tags=["mock"])

    # ------------------------------------------------------------------- #
    # 5. Execute the request
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    resp = client.get("/api/routers")
    try:
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert "routers" in data, "Missing 'routers' key in response"
        assert isinstance(data["routers"], list), "'routers' is not a list"
        assert len(data["routers"]) >= 2, "Expected at least two router entries"
    except AssertionError as exc:
        print(f"SELF‑TEST FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)