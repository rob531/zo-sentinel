import importlib.util
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "importlib_import_diagnostic"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

ZENTINEL_DIR = Path("/home/workspace/zo_sentinel")
MESH_DIR = Path("/home/workspace/zo_mesh")
DATASETS_DIR = Path("/home/workspace/Datasets")


def probe_module_spec(module_name: str) -> dict:
    """Probe a single module's import spec using importlib."""
    result = {
        "module_name": module_name,
        "found": False,
        "error": None,
        "spec": None,
        "parent": None,
    }
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            result["found"] = True
            result["spec"] = {
                "name": spec.name,
                "origin": spec.origin,
                "parent": spec.parent,
                "submodule_search_locations": spec.submodule_search_locations,
            }
        else:
            result["error"] = "Module spec not found"
    except Exception as e:
        result["error"] = str(e)
    return result


def try_import_module(module_name: str) -> dict:
    """Attempt to import a module and capture any errors."""
    result = {
        "module_name": module_name,
        "success": False,
        "error_type": None,
        "error_message": None,
        "traceback": None,
    }
    try:
        mod = importlib.import_module(module_name)
        result["success"] = True
        result["module"] = mod
    except Exception as e:
        result["error_type"] = type(e).__name__
        result["error_message"] = str(e)
        result["traceback"] = traceback.format_exc()
    return result


def scan_py_files_for_imports(directory: Path, extensions=None) -> dict:
    """Scan Python files for import statements to build dependency graph."""
    if extensions is None:
        extensions = [".py"]
    imports_found = {}
    py_files = list(directory.rglob("*.py"))
    
    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            local_imports = []
            from_imports = []
            
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("import ") and not stripped.startswith("import "):
                    continue
                if stripped.startswith("import "):
                    parts = stripped[7:].split(",")
                    for p in parts:
                        mod = p.strip().split(".")[0]
                        if mod and not mod.startswith("_"):
                            local_imports.append(mod)
                elif stripped.startswith("from "):
                    if " import " in stripped:
                        parts = stripped[5:].split(" import ")[0]
                        mod = parts.strip().split(".")[0]
                        if mod and not mod.startswith("_"):
                            from_imports.append(mod)
            
            rel_path = py_file.relative_to(directory)
            imports_found[str(rel_path)] = {
                "local_imports": list(set(local_imports)),
                "from_imports": list(set(from_imports)),
            }
        except Exception as e:
            logger.warning(f"Could not scan {py_file}: {e}")
    
    return imports_found


def detect_circular_imports(import_graph: dict) -> list:
    """Detect potential circular import chains in the dependency graph."""
    circular_chains = []
    
    def find_chain(node, visited, path):
        if node in path:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            return cycle
        if node in visited:
            return None
        
        visited.add(node)
        path.append(node)
        
        if node in import_graph:
            for dep in import_graph[node].get("local_imports", []) + import_graph[node].get("from_imports", []):
                chain = find_chain(dep, visited.copy(), path.copy())
                if chain:
                    circular_chains.append(chain)
        
        path.pop()
        return None
    
    for module in import_graph:
        find_chain(module, set(), [])
    
    return list(set(str(c) for c in circular_chains))


def probe_sentinel_modules() -> list:
    """Probe all Python modules in zo_sentinel directory."""
    results = []
    sentinel_modules = list(ZENTINEL_DIR.glob("*.py"))
    
    for py_file in sentinel_modules:
        module_name = py_file.stem
        logger.info(f"Probing module: {module_name}")
        
        probe_result = probe_module_spec(module_name)
        import_result = try_import_module(module_name)
        
        combined = {
            "module_name": module_name,
            "file_path": str(py_file),
            "spec_found": probe_result["found"],
            "spec_origin": probe_result["spec"]["origin"] if probe_result["spec"] else None,
            "import_success": import_result["success"],
            "import_error": import_result["error_message"] if not import_result["success"] else None,
            "import_traceback": import_result["traceback"] if not import_result["success"] else None,
        }
        results.append(combined)
        
        if not import_result["success"]:
            logger.error(f"FAILED to import {module_name}: {import_result['error_message']}")
            logger.debug(f"Traceback:\n{import_result['traceback']}")
        else:
            logger.info(f"OK: {module_name}")
    
    return results


def scan_recent_files_for_patterns() -> dict:
    """Scan recently-built files for common import issues."""
    recent_failures = ["registry_api.py", "rug_pull_monitor.py", "signal_analyser.py"]
    issues = {}
    
    for filename in recent_failures:
        filepath = ZENTINEL_DIR / filename
        if filepath.exists():
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                issues[filename] = {
                    "exists": True,
                    "has_from_import": "from " in content,
                    "has_relative_import": "from ." in content,
                    "has_importlib": "importlib" in content,
                    "lines_with_import": [i+1 for i, line in enumerate(content.split("\n")) if "import" in line.lower()],
                }
            except Exception as e:
                issues[filename] = {"exists": True, "error": str(e)}
        else:
            issues[filename] = {"exists": False}
    
    return issues


def main():
    """Run all import diagnostics."""
    logger.info("=" * 60)
    logger.info("ImportLib Import Diagnostic Probe")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    diagnostics = {}
    
    logger.info("\n[1] Probing sentinel modules...")
    sentinel_results = probe_sentinel_modules()
    diagnostics["sentinel_modules"] = sentinel_results
    
    failed_imports = [r for r in sentinel_results if not r["import_success"]]
    logger.info(f"\nFailed imports: {len(failed_imports)}")
    for fail in failed_imports:
        logger.error(f"  - {fail['module_name']}: {fail['import_error']}")
    
    logger.info("\n[2] Scanning for circular imports...")
    import_graph = scan_py_files_for_imports(ZENTINEL_DIR)
    diagnostics["dependency_graph"] = import_graph
    
    circular_chains = detect_circular_imports(import_graph)
    diagnostics["circular_chains"] = circular_chains
    logger.info(f"Potential circular import chains found: {len(circular_chains)}")
    for chain in circular_chains[:5]:
        logger.warning(f"  Chain: {chain}")
    
    logger.info("\n[3] Checking recent failure files...")
    recent_issues = scan_recent_files_for_patterns()
    diagnostics["recent_failures"] = recent_issues
    
    logger.info("\n[4] System paths analysis...")
    logger.info(f"sys.path: {sys.path[:5]}")
    
    sentinel_in_path = any("zo_sentinel" in p for p in sys.path)
    logger.info(f"Sentinel in sys.path: {sentinel_in_path}")
    
    if not sentinel_in_path:
        logger.warning("zo_sentinel not in sys.path - may cause import failures")
        logger.info("Adding /home/workspace/zo_sentinel to sys.path")
        sys.path.insert(0, str(ZENTINEL_DIR))
    
    logger.info("\n[5] Summary Report")
    logger.info("-" * 40)
    logger.info(f"Total modules probed: {len(sentinel_results)}")
    logger.info(f"Successful imports: {len([r for r in sentinel_results if r['import_success']])}")
    logger.info(f"Failed imports: {len(failed_imports)}")
    logger.info(f"Circular chains detected: {len(circular_chains)}")
    
    if failed_imports:
        logger.error("\nFAILED IMPORTS DETAIL:")
        for fail in failed_imports:
            logger.error(f"\n  Module: {fail['module_name']}")
            logger.error(f"  Error: {fail['import_error']}")
            if fail['import_traceback']:
                for line in fail['import_traceback'].split('\n')[-10:]:
                    logger.error(f"    {line}")
    
    logger.info("\n" + "=" * 60)
    logger.info("Diagnostic complete")
    logger.info("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())