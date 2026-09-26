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
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

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
    routers = []
    for path in root.rglob("router.py"):
        if "__pycache__" not in str(path) and "services/active/orphan_router_census" not in str(path):
            routers.append(path)
    return routers


def _extract_routes(router_file: Path) -> list[DefinedRoute]:
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
    module_name = router_file.stem

    for py_file in app_root.rglob("*.py"):
        if "__pycache__" in str(py_file) or py_file == router_file:
            continue
        try:
            content = py_file.read_text()
            if module_name in content:
                if re.search(rf'from\\s+[\\w.]+\\.{module_name}\\s+import', content):
                    return True
                if re.search(rf'import\\s+[\\w.]+\\.{module_name}(?!\\w)', content):
                    return True
        except Exception:
            continue
    return False


def _detect_orphans(app_root: Path) -> list[dict[str, Any]]:
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
def get_orphan_routers(db: Session = Depends(get_session)) -> OrphanRoutersResponse:
    app_root = _get_project_root()
    data = _detect_orphans(app_root)
    return OrphanRoutersResponse(
        orphan_routers=[OrphanRouterInfo(**item) for item in data]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    with tempfile.TemporaryDirectory() as tmpdir:
        t = Path(tmpdir)
        (t / "app").mkdir()
        (t / "app" / "routers").mkdir()

        used = t / "app" / "routers" / "used_router.py"
        used.write_text("""
from fastapi import APIRouter
router = APIRouter()
@router.get("/used")
def used_route():
    pass
""")
        consumer = t / "app" / "routers" / "consumer.py"
        consumer.write_text("from app.routers.used_router import router")

        orphan = t / "app" / "routers" / "orphan_router.py"
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

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        TestingSessionLocal = sessionmaker(bind=engine)

        def _override():
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = _override

        with test_app.app_context():
            data = _detect_orphans(t)

        orphan_filenames = [d["filename"] for d in data]
        passed = "orphan_router.py" in orphan_filenames and "used_router.py" not in orphan_filenames

        orphan_data = next((d for d in data if d["filename"] == "orphan_router.py"), None)
        if orphan_data and len(orphan_data.get("defined_routes", [])) == 2:
            pass
        else:
            passed = False

        if passed:
            print("PASS")
        else:
            print(f"FAIL: {orphan_filenames}")
