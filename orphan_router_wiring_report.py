#!/usr/bin/env python3
"""
Wiring completeness audit for server_* routers and app/routers/ against main.py.

Reads all router files in the repo, parses their route definitions,
compares against registered routes in app_router_registry.py and app/main.py,
and outputs a JSON report to shared/outputs/goose/orphan_wiring_audit.json.

No DB writes. Stdlib + pathlib only.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[0]
ROUTER_DIR = REPO_ROOT  # repo root: server_*_router.py files
APP_ROUTERS_DIR = REPO_ROOT / "app" / "routers"
APP_ROUTER_REGISTRY_FILE = REPO_ROOT / "app_router_registry.py"
MAIN_PY_FILE = REPO_ROOT / "app" / "main.py"
OUTPUT_FILE = REPO_ROOT / "shared" / "outputs" / "goose" / "orphan_wiring_audit.json"


def _parse_routes_from_source(source: str) -> list[dict]:
    """Parse route definitions from Python source using ast."""
    routes = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return routes

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            for dec in node.decorator_list:
                route_info = _extract_route_decorator(dec)
                if route_info:
                    route_info["function_name"] = node.name
                    route_info["line"] = node.lineno
                    routes.append(route_info)
    return routes


def _extract_route_decorator(dec: ast.expr) -> dict | None:
    """Extract method + path from a FastAPI route decorator node."""
    call = None
    if isinstance(dec, ast.Call):
        call = dec
        dec = dec.func

    if not isinstance(dec, ast.Attribute):
        return None

    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    method = None
    if dec.attr.lower() in http_methods:
        method = dec.attr.upper()
    else:
        return None

    path = "/"
    if call:
        args = call.args
        if args:
            if isinstance(args[0], ast.Constant):
                path = str(args[0].value)
            elif isinstance(args[0], ast.Str):  # Python < 3.8
                path = args[0].s

    return {"method": method, "path": path}


def _parse_router_name_from_filename(filename: str) -> str:
    """Derive the module name from a router filename."""
    return filename.removesuffix(".py")


def _get_wired_router_names() -> set[str]:
    """
    Extract the set of router module names that are wired into the app
    from app_router_registry.py and app/main.py.
    """
    wired: set[str] = set()

    # 1. Parse app_router_registry.py -- includes_app_routers function
    if APP_ROUTER_REGISTRY_FILE.exists():
        src = APP_ROUTER_REGISTRY_FILE.read_text(encoding="utf-8")
        # Look for import lines and function calls that register routers
        for line in src.splitlines():
            stripped = line.strip()
            # Match: from app.routers import (verdict_view_api, ...)
            if stripped.startswith("from app.routers import"):
                import re
                inner = stripped.split("import", 1)[1].strip().strip("()")
                names = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", inner)
                wired.update(names)
            # Match: app.include_router(verdict_view_api.router, ...)
            if "include_router" in stripped:
                import re
                names = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*\.router", stripped)
                for n in names:
                    wired.add(n.split(".")[0])

    # 2. Parse app/main.py -- _OPTIONAL_ROUTERS list + explicit imports
    if MAIN_PY_FILE.exists():
        src = MAIN_PY_FILE.read_text(encoding="utf-8")
        import re

        # _OPTIONAL_ROUTERS = ["server_compare_api", ...]
        match = re.search(
            r'_OPTIONAL_ROUTERS\s*=\s*\[(.*?)\]', src, re.DOTALL
        )
        if match:
            names = re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', match.group(1))
            wired.update(names)

        # explicit imports: from .server_axis_score_detail_api import router
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("from .") and "import" in stripped:
                m = re.match(r"from \.[a-zA-Z_]+ import (\w+)", stripped)
                if m:
                    name = m.group(1)
                    if name not in ("router",):
                        wired.add(name)

        # importlib loop: for _modname in _OPTIONAL_ROUTERS: app.include_router(_r)
        # the router names are already captured from _OPTIONAL_ROUTERS above

    return wired


def _scan_router_files(root_dir: pathlib.Path) -> dict[str, dict]:
    """
    Scan root_dir for *_router.py files and return a dict of
    {filename: {"module_name": str, "routes": [...]}}
    """
    results = {}
    if not root_dir.exists():
        return results

    for fpath in sorted(root_dir.glob("*_router.py")):
        try:
            src = fpath.read_text(encoding="utf-8")
        except Exception:
            continue
        module_name = _parse_router_name_from_filename(fpath.name)
        routes = _parse_routes_from_source(src)
        results[fpath.name] = {"module_name": module_name, "routes": routes}
    return results


def _build_audit() -> dict:
    """Build the full audit report dict."""
    wired_names = _get_wired_router_names()

    # Scan repo-root server_*_router.py files
    root_routers = _scan_router_files(ROUTER_DIR)

    # Scan app/routers/ directory
    app_routers_dir = _scan_router_files(APP_ROUTERS_DIR)

    all_routers: dict[str, dict] = {}
    all_routers.update(root_routers)
    all_routers.update(app_routers_dir)

    wired_count = 0
    orphan_count = 0
    orphans: list[dict] = []

    for filename, info in all_routers.items():
        module_name = info["module_name"]
        routes = info["routes"]

        if module_name in wired_names:
            wired_count += 1
        else:
            orphan_count += 1
            orphans.append({"filename": filename, "routes": routes})

    total_routers = len(all_routers)

    return {
        "total_routers": total_routers,
        "wired_count": wired_count,
        "orphan_count": orphan_count,
        "orphans": orphans,
    }


def main() -> int:
    output_dir = OUTPUT_FILE.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    report = _build_audit()

    OUTPUT_FILE.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
