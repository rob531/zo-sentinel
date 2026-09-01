from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from fastapi.routing import APIRoute, Mount
import inspect

from app.db import get_session


class OrphanRouteInfo(BaseModel):
    router_name: str
    module_path: str
    endpoint_path: str
    mount_point: Optional[str]


class OrphanRouterCensusResponse(BaseModel):
    total_routers: int
    orphan_routes: List[OrphanRouteInfo]
    checked_at: datetime


class OrphanRouterCensusLogic:
    def __init__(self, app: Any):
        self.app = app
        self._mounted_routers: Optional[List[Dict[str, Any]]] = None
        self._mounted_modules: Optional[set] = None

    def _get_mounted_modules(self) -> set:
        if self._mounted_modules is not None:
            return self._mounted_modules
        self._find_mounted_routers()
        return self._mounted_modules

    def _find_mounted_routers(self) -> None:
        mounted = []
        mounted_modules = set()
        for route in self.app.routes:
            if isinstance(route, Mount):
                mounted.append({"path": route.path, "mount": route})
                if hasattr(route, "app") and hasattr(route.app, "routes"):
                    for r in route.app.routes:
                        if isinstance(r, APIRoute) and r.endpoint:
                            mod = inspect.getmodule(r.endpoint)
                            if mod:
                                mounted_modules.add(mod.__name__)
        self._mounted_routers = mounted
        self._mounted_modules = mounted_modules

    def enumerate_all_routers(self) -> List[Dict[str, Any]]:
        all_routers = []
        for route in self.app.routes:
            if isinstance(route, APIRoute):
                mount_point = None
                if self._mounted_routers is None:
                    self._find_mounted_routers()
                if self._mounted_routers:
                    for mr in sorted(self._mounted_routers, key=lambda x: len(x["path"]), reverse=True):
                        check_path = route.path
                        if check_path.startswith(mr["path"] + "/") or check_path == mr["path"]:
                            mount_point = mr["path"]
                            break
                all_routers.append({
                    "router_name": route.name,
                    "module_path": inspect.getmodule(route.endpoint).__name__ if inspect.getmodule(route.endpoint) else "unknown",
                    "endpoint_path": route.path,
                    "mount_point": mount_point
                })
        return all_routers

    def find_orphaned_routers(self) -> List[OrphanRouteInfo]:
        if self._mounted_modules is None:
            self._find_mounted_routers()
        mounted_modules = self._mounted_modules
        orphan_routes = []
        for route in self.app.routes:
            if isinstance(route, APIRoute):
                endpoint_module = inspect.getmodule(route.endpoint)
                if endpoint_module:
                    mod_name = endpoint_module.__name__
                    is_mounted = mod_name in mounted_modules
                    is_system = mod_name in ("starlette.routing", "fastapi.routing") or "test" in mod_name
                    if not is_mounted and not is_system:
                        orphan_routes.append(OrphanRouteInfo(
                            router_name=route.name,
                            module_path=mod_name,
                            endpoint_path=route.path,
                            mount_point=self._get_mount_point(route.path)
                        ))
        return orphan_routes

    def _get_mount_point(self, path: str) -> Optional[str]:
        if self._mounted_routers is None:
            self._find_mounted_routers()
        if self._mounted_routers:
            for mr in sorted(self._mounted_routers, key=lambda x: len(x["path"]), reverse=True):
                if path.startswith(mr["path"] + "/") or path == mr["path"]:
                    return mr["path"]
        return None

    def enumerate_orphans(self) -> OrphanRouterCensusResponse:
        all_routers = self.enumerate_all_routers()
        orphan_routes = self.find_orphaned_routers()
        return OrphanRouterCensusResponse(
            total_routers=len(all_routers),
            orphan_routes=orphan_routes,
            checked_at=datetime.now(timezone.utc)
        )


def get_census_report() -> OrphanRouterCensusResponse:
    from app.router import app as main_app
    logic = OrphanRouterCensusLogic(app=main_app)
    return logic.enumerate_orphans()


router = APIRouter()


@router.get("/reports/orphan-router-census", response_model=OrphanRouterCensusResponse)
def orphan_router_census(session=Depends(get_session)):
    return get_census_report()


if __name__ == "__main__":
    from fastapi import FastAPI
    from starlette.routing import Route

    test_app = FastAPI()

    mounted_r = APIRouter()
    @mounted_r.get("/test")
    def mounted_handler():
        pass

    orphan_r = APIRouter()
    @orphan_r.get("/orphan")
    def orphan_handler():
        pass

    test_app.include_router(mounted_r, prefix="/api")

    logic = OrphanRouterCensusLogic(app=test_app)
    result = logic.enumerate_orphans()

    assert result.total_routers == 1, f"Expected 1 total router, got {result.total_routers}"
    assert len(result.orphan_routes) == 1, f"Expected 1 orphan, got {len(result.orphan_routes)}"
    print("PASS")