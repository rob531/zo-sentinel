"""unmounted_router_census service contract."""
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List

from fastapi import APIRouter, FastAPI
from fastapi.routes import APIRoute
from pydantic import BaseModel

from app.db import get_session
from app.models import Org

FILE_DIR = Path(__file__).parent


class UnmountedRouter(BaseModel):
    router_name: str
    registered_at: datetime
    reason: str


class CensusResponse(BaseModel):
    total_mounted: int
    total_registered: int
    unmounted: List[UnmountedRouter]


def get_mounted_router_names(app: FastAPI) -> set:
    """Extract mounted router names from FastAPI app.routes."""
    mounted = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            if hasattr(route, "path") and route.path:
                mounted.add(route.path)
    return mounted


def get_router_registry() -> List[dict]:
    """Read router registry from app/router_registry.py."""
    try:
        from app import router_registry as rr_module
        if hasattr(rr_module, 'get_router_registry'):
            return rr_module.get_router_registry()
        return []
    except (ImportError, AttributeError):
        return []


def get_unmounted_routers(app: FastAPI) -> dict:
    """
    Compare router registry against mounted routes.
    Returns census data with mounted count, registered count, and unmounted list.
    """
    registry = get_router_registry()
    mounted_names = get_mounted_router_names(app)
    
    unmounted = []
    for entry in registry:
        router_name = entry.get("name") or entry.get("router_name")
        endpoint = entry.get("endpoint", "")
        if router_name and endpoint not in mounted_names:
            registered_at_str = entry.get("registered_at", "")
            try:
                registered_at = datetime.fromisoformat(registered_at_str)
            except (ValueError, TypeError):
                registered_at = datetime.now()
            unmounted.append(UnmountedRouter(
                router_name=router_name,
                registered_at=registered_at,
                reason="Router registered but not mounted on app"
            ))
    
    return CensusResponse(
        total_mounted=len(mounted_names),
        total_registered=len(registry),
        unmounted=unmounted
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI()
    router = APIRouter()
    
    @router.get("/api/census/unmounted-routers")
    def get_router_census() -> CensusResponse:
        from app.main import app as main_app
        return get_unmounted_routers(main_app)
    
    app.include_router(router)
    return app


app = create_app()


def get_router_census() -> CensusResponse:
    """Public API for other services to get router census."""
    from app.main import app as main_app
    return get_unmounted_routers(main_app)


if __name__ == "__main__":
    import json
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    mock_registry_data = [
        {"name": "test_router1", "endpoint": "/api/test1", "registered_at": "2024-01-01T00:00:00", "description": "Router 1"},
        {"name": "test_router2", "endpoint": "/api/test2", "registered_at": "2024-01-02T00:00:00", "description": "Router 2"},
        {"name": "test_router3", "endpoint": "/api/test3", "registered_at": "2024-01-03T00:00:00", "description": "Router 3"},
        {"name": "test_router4", "endpoint": "/api/test4", "registered_at": "2024-01-04T00:00:00", "description": "Router 4"},
        {"name": "test_router5", "endpoint": "/api/test5", "registered_at": "2024-01-05T00:00:00", "description": "Router 5"},
    ]
    
    mock_rr_module = MagicMock()
    mock_rr_module.get_router_registry = MagicMock(return_value=mock_registry_data)
    sys.modules["app.router_registry"] = mock_rr_module
    
    test_app = FastAPI()
    test_router = APIRouter()
    
    @test_router.get("/api/test1")
    async def test_endpoint1():
        return {"router": "test1"}
    
    @test_router.get("/api/test2")
    async def test_endpoint2():
        return {"router": "test2"}
    
    @test_router.get("/api/test3")
    async def test_endpoint3():
        return {"router": "test3"}
    
    test_app.include_router(test_router)
    
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    local_router = APIRouter()
    
    @local_router.get("/api/census/unmounted-routers")
    def local_census():
        return get_unmounted_routers(test_app)
    
    test_app.include_router(local_router)
    
    client = TestClient(test_app)
    response = client.get("/api/census/unmounted-routers")
    
    assert response.status_code == 200
    data = response.json()
    assert data["total_mounted"] == 3
    assert data["total_registered"] == 5
    assert len(data["unmounted"]) == 2
    
    print("PASS")