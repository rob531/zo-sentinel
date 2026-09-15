import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class MountedRouter(BaseModel):
    name: str
    path: str


class UnmountedRouter(BaseModel):
    name: str
    path: str
    stage: str  # "active" or "staged"


class OrphanedRouter(BaseModel):
    name: str
    path: str


class RouterCensusSummary(BaseModel):
    total: int
    mounted_count: int
    unmounted_count: int
    orphaned_count: int


class RouterCensusResponse(BaseModel):
    mounted: list[MountedRouter]
    unmounted: list[UnmountedRouter]
    orphaned: list[OrphanedRouter]
    summary: RouterCensusSummary


def _extract_router_name_from_source(source: str) -> Optional[str]:
    """Extract router name from router source file."""
    patterns = [
        r'router\s*=\s*APIRouter\(\s*(?:.*?name\s*=\s*["\']([^"\']+)["\'])?',
        r'router\s*=\s*APIRouter\(["\']([^"\']+)["\']',
        r'@router\.(?:get|post|put|delete|patch)\(["\']([^"\']+)["\']',
        r'router_name\s*=\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            if match.groups():
                return match.group(1) if match.group(1) else None
    return None


def _get_registered_router_names(registry_path: Path) -> set[str]:
    """Read app_router_registry.py and extract registered router names."""
    if not registry_path.exists():
        return set()
    
    source = registry_path.read_text()
    
    # Match patterns like: "router_name" = <something> or "router_name": <something>
    pattern = r'["\'](\w+)["\']\s*[=:]\s*'
    matches = re.findall(pattern, source)
    
    return set(matches)


def _glob_router_files(base_path: Path) -> list[tuple[str, Path, str]]:
    """Glob all router.py files from services/active and services/staged.
    
    Returns list of (name, path, stage) tuples.
    """
    results = []
    
    for stage in ["active", "staged"]:
        stage_path = base_path / "services" / stage
        if not stage_path.exists():
            continue
        
        for router_file in stage_path.glob("*/router.py"):
            try:
                source = router_file.read_text()
                name = _extract_router_name_from_source(source)
                if name:
                    results.append((name, str(router_file), stage))
                else:
                    # Try to get name from directory
                    dir_name = router_file.parent.name
                    results.append((dir_name, str(router_file), stage))
            except Exception:
                continue
    
    return results


def get_unmounted_routers(base_path: Optional[Path] = None) -> RouterCensusResponse:
    """Get census of mounted vs unmounted routers.
    
    - mounted: routers registered in app_router_registry.py and have a file
    - unmounted: routers with files but NOT registered (and not orphaned)
    - orphaned: routers registered but files don't exist
    """
    if base_path is None:
        base_path = Path("/home/workspace/zo_sentinel")
    
    # Get app router registry path
    registry_path = base_path / "app" / "router_registry.py"
    
    # Get registered router names
    registered_names = _get_registered_router_names(registry_path)
    
    # Get all router files
    router_files = _glob_router_files(base_path)
    
    # Separate into mounted, unmounted
    mounted = []
    unmounted = []
    found_names = set()
    
    for name, path, stage in router_files:
        found_names.add(name)
        if name in registered_names:
            mounted.append(MountedRouter(name=name, path=path))
        else:
            unmounted.append(UnmountedRouter(name=name, path=path, stage=stage))
    
    # Orphaned: registered but no file exists
    orphaned_names = registered_names - found_names
    orphaned = [OrphanedRouter(name=name, path="<no file>") for name in orphaned_names]
    
    total = len(mounted) + len(unmounted) + len(orphaned)
    
    return RouterCensusResponse(
        mounted=mounted,
        unmounted=unmounted,
        orphaned=orphaned,
        summary=RouterCensusSummary(
            total=total,
            mounted_count=len(mounted),
            unmounted_count=len(unmounted),
            orphaned_count=len(orphaned),
        ),
    )