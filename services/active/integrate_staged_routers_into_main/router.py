# deps: fastapi, pydantic, sqlalchemy
"""Integrate staged routers into main application."""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["integrate_staged_routers_into_main"])

STAGED_DIR = Path(__file__).parent.parent.parent / "staged"
logger = logging.getLogger(__name__)


class StagedServiceInfo(BaseModel):
    name: str
    module: str
    path: str
    has_router: bool
    has_contract: bool
    has_logic: bool


class IntegrationResult(BaseModel):
    success: bool
    total_discovered: int
    services: list[StagedServiceInfo]


class StatusResponse(BaseModel):
    service: str
    integration: IntegrationResult


def _discover_staged_services() -> list[tuple[str, Path]]:
    """Discover all staged service directories."""
    services = []
    if STAGED_DIR.exists():
        for entry in STAGED_DIR.iterdir():
            if entry.is_dir() and (entry / "__init__.py").exists():
                services.append((entry.name, entry))
    return services


def _check_service_files(path: Path) -> dict[str, bool]:
    """Check which service files exist."""
    return {
        "has_router": (path / "router.py").exists(),
        "has_contract": (path / "contract.py").exists(),
        "has_logic": (path / "logic.py").exists(),
    }


def _import_service_router(name: str) -> bool:
    """Try to import a service router, return True if it has a valid router."""
    module_name = f"services.staged.{name}.router"
    try:
        mod = importlib.import_module(module_name)
        return hasattr(mod, "router")
    except (ImportError, Exception):
        return False


@router.get("/integrate/status", response_model=StatusResponse)
def get_integration_status(
    db: Session = Depends(get_session),
) -> StatusResponse:
    """Get status of staged router integration."""
    discovered = _discover_staged_services()
    services = []

    for name, path in discovered:
        files = _check_service_files(path)
        has_router = _import_service_router(name)
        services.append(
            StagedServiceInfo(
                name=name,
                module=f"services.staged.{name}",
                path=str(path),
                has_router=has_router,
                has_contract=files["has_contract"],
                has_logic=files["has_logic"],
            )
        )

    return StatusResponse(
        service="integrate_staged_routers_into_main",
        integration=IntegrationResult(
            success=True,
            total_discovered=len(services),
            services=services,
        ),
    )


@router.get("/integrate/services", response_model=list[StagedServiceInfo])
def list_staged_services(
    db: Session = Depends(get_session),
) -> list[StagedServiceInfo]:
    """List all discovered staged services."""
    discovered = _discover_staged_services()
    services = []

    for name, path in discovered:
        files = _check_service_files(path)
        has_router = _import_service_router(name)
        services.append(
            StagedServiceInfo(
                name=name,
                module=f"services.staged.{name}",
                path=str(path),
                has_router=has_router,
                has_contract=files["has_contract"],
                has_logic=files["has_logic"],
            )
        )

    return services


@router.post("/integrate/mount/{service_name}")
def mount_staged_service(
    service_name: str,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Mount a specific staged service router."""
    module_name = f"services.staged.{service_name}.router"
    try:
        mod = importlib.import_module(module_name)
        if not hasattr(mod, "router"):
            raise HTTPException(
                status_code=400,
                detail=f"Service {service_name} does not have a router attribute",
            )
        return {
            "status": "ready",
            "service": service_name,
            "module": module_name,
            "router": str(mod.router),
        }
    except ImportError as e:
        raise HTTPException(
            status_code=404,
            detail=f"Service {service_name} not found: {e}",
        )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import tempfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory test DB
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Create test app with dependency override
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    # Test status endpoint
    resp = client.get("/api/integrate/status")
    assert resp.status_code == 200, f"Status failed: {resp.text}"
    data = resp.json()
    assert "integration" in data
    assert "total_discovered" in data["integration"]
    assert "services" in data["integration"]

    # Test list endpoint
    resp = client.get("/api/integrate/services")
    assert resp.status_code == 200, f"List failed: {resp.text}"
    assert isinstance(resp.json(), list)

    # Test mount endpoint with non-existent service
    resp = client.post("/api/integrate/mount/nonexistent_service_xyz")
    assert resp.status_code == 404, f"Expected 404 for nonexistent service, got {resp.status_code}"

    print("PASS")
