# deps: fastapi, sqlalchemy, pydantic
"""Router for orphan_router_census service.

Detects routers defined in the codebase that are never imported/used by any
other module. Performs AST analysis to find import statements referencing
router.py files and compares against registered router definitions.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["orphan_router_census"])


class DefinedRoute(BaseModel):
    path: str
    methods: list[str]


class OrphanRouterInfo(BaseModel):
    filename: str
    path: str
    defined_routes: list[DefinedRoute]


class OrphanRoutersResponse(BaseModel):
    orphan_routers: list[OrphanRouterInfo]


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _find_router_files(root: Path) -> list[Path]:
    """Find all router.py files under root."""
    routers = []
    for path in root.rglob("router.py"):
        if "__pycache__" not in str(path):
            routers.append(path)
    return routers


def _extract_routes(router_file: Path) -> list[DefinedRoute]:
    """Extract route definitions from a router file."""
    routes = []
    try:
        content = router_file.read_text()
        pattern = r'@router\.(get|post|put|delete|patch|options|head)\s*\(\s*["\']([^"\']+)["\']'
        for method, path in re.findall(pattern, content):
            routes.append(DefinedRoute(path=path, methods=[method.upper()]))
    except Exception:
        pass
    return routes


def _is_imported(router_file: Path, app_root: Path) -> bool:
    """Check if a router file is imported by any other module."""
    rel = router_file.relative_to(app_root)
    parts = list(rel.parts[:-1])  # e.g. ["services","active","orphan_router_census"]
    if parts:
        module_path = ".".join(parts)  # "services.active.orphan_router_census"
    else:
        return False

    for py_file in app_root.rglob("*.py"):
        if "__pycache__" in str(py_file) or py_file == router_file:
            continue
        try:
            content = py_file.read_text()
            if re.search(rf'from\s+{re.escape(module_path)}(\.router)?\s', content):
                return True
            if re.search(rf'import\s+{re.escape(module_path)}(\.router)?\s', content):
                return True
        except Exception:
            continue
    return False


def _detect_orphans(app_root: Path) -> list[dict[str, Any]]:
    """Detect orphan router.py files that are never imported."""
    orphans = []
    for rf in _find_router_files(app_root):
        if not _is_imported(rf, app_root):
            routes = _extract_routes(rf)
            orphans.append({
                "filename": rf.name,
                "path": str(rf.relative_to(app_root)),
                "defined_routes": [r.model_dump() for r in routes]
            })
    return orphans


@router.get("/diagnostics/orphan-routers", response_model=OrphanRoutersResponse)
def get_orphan_routers(db: "Session" = Depends(lambda: None)) -> OrphanRoutersResponse:  # type: ignore[assignment]
    """Detect orphan routers in the codebase.

    This endpoint scans the project for router.py files and identifies
    those that are never imported or used by any other module.
    """
    app_root = _get_project_root()
    data = _detect_orphans(app_root)
    return OrphanRoutersResponse(
        orphan_routers=[OrphanRouterInfo(**item) for item in data]
    )


if __name__ == "__main__":
    # Self-test: verify orphan detection logic works correctly
    with tempfile.TemporaryDirectory() as tmpdir:
        t = Path(tmpdir)
        (t / "apps" / "used_service").mkdir(parents=True)
        (t / "apps" / "orphan_service").mkdir(parents=True)

        # Create a router that IS imported (not orphan)
        used = t / "apps" / "used_service" / "router.py"
        used.write_text("""
from fastapi import APIRouter
router = APIRouter()
@router.get("/used")
def used_route():
    pass
""")
        # Consumer imports the used router
        consumer = t / "apps" / "used_service" / "consumer.py"
        consumer.write_text("from apps.used_service.router import router")

        # Create an orphan router (not imported anywhere)
        orphan = t / "apps" / "orphan_service" / "router.py"
        orphan.write_text("""
from fastapi import APIRouter
router = APIRouter()
@router.get("/orphan1")
def orphan1():
    pass
@router.post("/orphan2")
def orphan2():
    pass
""")

        # Run detection on temp directory
        data = _detect_orphans(t)

        orphan_filenames = [d["filename"] for d in data]
        passed = "router.py" in orphan_filenames

        orphan_data = next((d for d in data if "orphan_service" in d["path"]), None)
        if orphan_data and len(orphan_data.get("defined_routes", [])) == 2:
            pass
        else:
            passed = False

        if passed:
            print("PASS")
        else:
            print(f"FAIL: orphan_filenames={orphan_filenames}, data={data}")
            exit(1)
