import logging
import sys
import os
import importlib
import inspect
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any
from pathlib import Path
import requests

# Configuration
SERVICE_NAME = "importlib_import_diagnostic"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
LOG_FILE = "/home/workspace/logs/importlib_import_diagnostic.log"

# Module-level logger
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.DEBUG)

# File handler only (daemon logging pattern)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"sql": sql, "params": params if params else []},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to DuckDB via write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    """Send service heartbeat to write_service."""
    ts = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": ts,
        "meta": meta
    }])


def get_module_path(module_name: str) -> Optional[str]:
    """Get the file path for a module."""
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, '__file__', None)
    except Exception:
        return None


def get_imports_from_source(file_path: str) -> Set[str]:
    """Parse source file for import statements."""
    imports = set()
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        for line in content.split('\n'):
            stripped = line.strip()
            # import x.y.z
            if stripped.startswith('import '):
                parts = stripped[7:].split(',')
                for part in parts:
                    module = part.strip().split('.')[0]
                    if module:
                        imports.add(module)
            # from x.y.z import ...
            elif stripped.startswith('from '):
                try:
                    rest = stripped[5:]
                    module = rest.split('.')[0].split()[0]
                    if module and module not in ('__future__',):
                        imports.add(module)
                except IndexError:
                    pass
    except Exception as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
    return imports


def test_module_import(module_name: str) -> Dict[str, Any]:
    """Test importing a single module and capture any errors."""
    result = {
        "module_name": module_name,
        "imported": False,
        "error_type": None,
        "error_message": None,
        "traceback": None,
        "module_path": None
    }
    
    try:
        mod = importlib.import_module(module_name)
        result["imported"] = True
        result["module_path"] = getattr(mod, '__file__', 'built-in/namespace')
        logger.info(f"Successfully imported {module_name}")
    except ImportError as e:
        result["error_type"] = "ImportError"
        result["error_message"] = str(e)
        result["traceback"] = traceback.format_exc()
        logger.warning(f"ImportError for {module_name}: {e}")
    except Exception as e:
        result["error_type"] = type(e).__name__
        result["error_message"] = str(e)
        result["traceback"] = traceback.format_exc()
        logger.error(f"Error importing {module_name}: {e}")
    
    return result


def build_dependency_graph(target_modules: List[str]) -> Dict[str, Any]:
    """Build a dependency graph for target modules."""
    graph = {
        "nodes": {},
        "edges": [],
        "circular_refs": [],
        "missing_modules": []
    }
    
    visited = set()
    path = []
    
    def visit(module_name: str) -> bool:
        """Recursively visit module with cycle detection."""
        if module_name in path:
            cycle_start = path.index(module_name)
            cycle = path[cycle_start:] + [module_name]
            graph["circular_refs"].append(cycle)
            return False
        
        if module_name in visited:
            return True
        
        visited.add(module_name)
        path.append(module_name)
        
        # Get module path and parse imports
        mod_path = get_module_path(module_name)
        imports = set()
        
        if mod_path and Path(mod_path).suffix == '.py':
            imports = get_imports_from_source(mod_path)
        
        graph["nodes"][module_name] = {
            "path": mod_path,
            "imports": list(imports)
        }
        
        # Test each import
        for imp in imports:
            if imp in target_modules or imp.startswith('zo_sentinel'):
                # Track edge
                edge = {"from": module_name, "to": imp}
                if edge not in graph["edges"]:
                    graph["edges"].append(edge)
                
                # Recursively visit
                visit(imp)
        
        path.pop()
        return True
    
    for module in target_modules:
        visit(module)
    
    return graph


def check_registry_module_health() -> Dict[str, Any]:
    """Check health of modules from mcp_server_registry."""
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "modules": []
    }
    
    # Query registry for modules
    modules = ws_query("""
        SELECT server_id, server_name, server_type, metadata 
        FROM mcp_server_registry 
        WHERE metadata IS NOT NULL 
        LIMIT 50
    """)
    
    for row in modules:
        server_name = row.get("server_name", "")
        server_id = row.get("server_id", "")
        
        # Try to import the module
        import_result = test_module_import(server_name)
        import_result["server_id"] = server_id
        
        # Parse metadata for additional info
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            import_result["metadata_str"] = metadata[:200]
        else:
            import_result["metadata"] = metadata
        
        result["modules"].append(import_result)
    
    return result


def check_specific_failing_modules() -> Dict[str, Any]:
    """Check the specifically mentioned failing modules."""
    target_modules = [
        "registry_api",
        "rug_pull_monitor", 
        "signal_analyser"
    ]
    
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "target_modules": []
    }
    
    for mod in target_modules:
        # Try full import path
        full_name = f"zo_sentinel.{mod}" if not mod.startswith("zo_sentinel") else mod
        test_result = test_module_import(mod)
        test_result["tried_full_name"] = full_name
        
        # Build partial dependency graph for this module
        dep_graph = build_dependency_graph([mod])
        test_result["dependency_graph"] = dep_graph
        
        result["target_modules"].append(test_result)
    
    return result


def check_circular_imports(modules: List[str]) -> List[List[str]]:
    """Detect circular imports in module list."""
    circular = []
    
    # Build full dependency map
    deps = {}
    for mod in modules:
        path = get_module_path(mod)
        if path and Path(path).suffix == '.py':
            imports = get_imports_from_source(path)
            deps[mod] = imports
    
    # DFS cycle detection
    def has_cycle(start: str, visited: Set[str], stack: List[str]) -> Optional[List[str]]:
        visited.add(start)
        stack.append(start)
        
        for dep in deps.get(start, []):
            if dep in deps:
                if dep in stack:
                    cycle_start = stack.index(dep)
                    return stack[cycle_start:] + [dep]
                if dep not in visited:
                    cycle = has_cycle(dep, visited, stack[:])
                    if cycle:
                        return cycle
        
        return None
    
    for mod in modules:
        cycle = has_cycle(mod, set(), [])
        if cycle and cycle not in circular:
            circular.append(cycle)
    
    return circular


def generate_diagnostic_report() -> Dict[str, Any]:
    """Generate comprehensive diagnostic report."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sys_version": sys.version,
        "sys_path": sys.path[:5],
        "target_module_diagnostics": None,
        "registry_health": None,
        "circular_imports": None,
        "missing_dependencies": None,
        "recommendations": []
    }
    
    # Check specific failing modules
    logger.info("Checking specific failing modules...")
    report["target_module_diagnostics"] = check_specific_failing_modules()
    
    # Check registry health
    logger.info("Checking registry module health...")
    report["registry_health"] = check_registry_module_health()
    
    # Check for circular imports
    logger.info("Checking for circular imports...")
    target_modules = ["registry_api", "rug_pull_monitor", "signal_analyser"]
    report["circular_imports"] = check_circular_imports(target_modules)
    
    # Analyze missing dependencies
    missing = []
    for mod_result in report["target_module_diagnostics"]:
        if not mod_result["imported"]:
            missing.append({
                "module": mod_result["module_name"],
                "error": mod_result["error_message"],
                "error_type": mod_result["error_type"]
            })
    
    report["missing_dependencies"] = missing
    
    # Generate recommendations
    if missing:
        report["recommendations"].append(f"Found {len(missing)} modules with import errors. Check missing dependencies first.")
    
    if report["circular_imports"]:
        report["recommendations"].append(f"Found {len(report['circular_imports'])} circular import chains.")
    
    if not report["target_module_diagnostics"]["target_modules"]:
        report["recommendations"].append("No target modules could be loaded - check Python path configuration.")
    
    return report


def write_report_to_db(report: Dict[str, Any]) -> bool:
    """Write diagnostic report to DuckDB via write_service."""
    rows = [{
        "diagnostic_id": f"importlib_diag_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "generated_at": report["generated_at"],
        "target_modules_checked": len(report.get("target_module_diagnostics", {}).get("target_modules", [])),
        "circular_imports_found": len(report.get("circular_imports", [])),
        "missing_dependencies_count": len(report.get("missing_dependencies", [])),
        "recommendations": str(report.get("recommendations", []))[:500],
        "report_json": str(report)[:2000]
    }]
    
    return ws_write("importlib_diagnostics", rows)


def main():
    """Main diagnostic execution."""
    logger.info("=" * 60)
    logger.info("Starting importlib diagnostic run")
    logger.info("=" * 60)
    
    try:
        # Generate comprehensive report
        report = generate_diagnostic_report()
        
        # Log report summary
        logger.info(f"Target modules checked: {len(report['target_module_diagnostics']['target_modules'])}")
        logger.info(f"Circular imports found: {len(report['circular_imports'])}")
        logger.info(f"Missing dependencies: {len(report['missing_dependencies'])}")
        
        for rec in report.get("recommendations", []):
            logger.info(f"Recommendation: {rec}")
        
        # Write to database
        write_report_to_db(report)
        
        # Print summary to stdout for smoke test capture
        print(f"IMPORTLIB_DIAGNOSTIC_COMPLETE")
        print(f"target_modules_checked={len(report['target_module_diagnostics']['target_modules'])}")
        print(f"circular_imports={len(report['circular_imports'])}")
        print(f"missing_deps={len(report['missing_dependencies'])}")
        
        for missing in report["missing_dependencies"]:
            print(f"MISSING: {missing['module']} - {missing['error']}")
        
        send_heartbeat("completed", f"checked={len(report['target_module_diagnostics']['target_modules'])}")
        
        logger.info("Diagnostic run completed successfully")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Diagnostic run failed: {e}")
        traceback.print_exc()
        send_heartbeat("failed", str(e)[:200])
        sys.exit(1)


if __name__ == "__main__":
    main()