import sys
import os
import traceback
import importlib
import importlib.util
import inspect
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, List, Set, Tuple

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
PROBLEM_FILES = [
    "registry_api.py",
    "rug_pull_monitor.py", 
    "signal_analyser.py",
]

class ImportNode:
    def __init__(self, name: str, file_path: Optional[str] = None):
        self.name = name
        self.file_path = file_path
        self.dependencies: List[str] = []
        self.failed: bool = False
        self.error: Optional[str] = None
        self.level: int = 0

class ImportChainTracer:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.nodes: Dict[str, ImportNode] = {}
        self.chain_cache: Dict[str, List[str]] = {}
        self.failed_imports: Set[str] = set()
        self.successful_imports: Set[str] = set()
        
    def find_module_path(self, module_name: str) -> Optional[Path]:
        """Find the file path for a module."""
        parts = module_name.split('.')
        search_paths = []
        
        for part in parts[:-1]:
            search_paths.append(part)
        
        base = self.project_root
        for sp in search_paths:
            candidate = base / sp
            if candidate.is_dir():
                base = candidate
            else:
                return None
        
        init_file = base / "__init__.py"
        if init_file.exists():
            return init_file
            
        module_file = base / f"{parts[-1]}.py"
        if module_file.exists():
            return module_file
            
        return None
    
    def extract_imports(self, file_path: Path) -> List[str]:
        """Extract all import statements from a file."""
        imports = []
        try:
            content = file_path.read_text()
            lines = content.split('\n')
            
            for line in lines:
                line = line.strip()
                if line.startswith('import ') and not line.startswith('import ') and '#' not in line.split('#')[0]:
                    parts = line.split()
                    if len(parts) >= 2:
                        module = parts[1].split('(')[0].rstrip(',;')
                        if module and not module.startswith('_'):
                            imports.append(module.split('.')[0])
                elif line.startswith('from '):
                    parts = line.split()
                    if len(parts) >= 2:
                        module = parts[1].split('(')[0].rstrip(',;')
                        if module and not module.startswith('_'):
                            imports.append(module.split('.')[0])
        except Exception as e:
            print(f"  Warning: Could not read {file_path}: {e}")
        return imports
    
    def test_import(self, module_name: str) -> Tuple[bool, Optional[str]]:
        """Test importing a module and capture any errors."""
        if module_name in self.successful_imports:
            return True, None
        if module_name in self.failed_imports:
            return False, "Previously failed"
            
        original_modules = set(sys.modules.keys())
        
        try:
            importlib.import_module(module_name)
            if module_name not in sys.modules or sys.modules[module_name] is None:
                raise ImportError(f"Module {module_name} imported but not in sys.modules")
            self.successful_imports.add(module_name)
            return True, None
        except Exception as e:
            tb = traceback.format_exc()
            self.failed_imports.add(module_name)
            return False, f"{type(e).__name__}: {e}\n{tb[-500:]}"
    
    def trace_file_imports(self, file_path: Path, depth: int = 0, visited: Optional[Set] = None) -> List[ImportNode]:
        """Recursively trace imports for a file."""
        if visited is None:
            visited = set()
        
        if str(file_path) in visited:
            return []
        visited.add(str(file_path))
        
        imports = self.extract_imports(file_path)
        nodes = []
        
        for imp in imports:
            if imp.startswith('_') or imp in sys.stdlib_module_names:
                continue
                
            path = self.find_module_path(imp)
            node = ImportNode(imp, str(path) if path else None)
            node.level = depth
            
            success, error = self.test_import(imp)
            node.failed = not success
            node.error = error
            
            nodes.append(node)
            
            if success and path:
                sub_nodes = self.trace_file_imports(path, depth + 1, visited)
                nodes.extend(sub_nodes)
        
        return nodes
    
    def analyze_file(self, filename: str) -> Dict:
        """Complete analysis of a file's import chain."""
        file_path = self.project_root / filename
        
        if not file_path.exists():
            return {"error": f"File not found: {filename}"}
        
        result = {
            "file": filename,
            "path": str(file_path),
            "nodes": [],
            "failed_count": 0,
            "success_count": 0,
        }
        
        print(f"\n{'='*60}")
        print(f"Analyzing: {filename}")
        print(f"{'='*60}")
        
        original_modules = set(sys.modules.keys())
        
        try:
            spec = importlib.util.spec_from_file_location("__target__", file_path)
            if spec and spec.loader:
                source_module = importlib.util.module_from_spec(spec)
                result["nodes"].append({
                    "name": f"EXECUTE:{filename}",
                    "level": 0,
                    "status": "loading...",
                })
                
                try:
                    spec.loader.exec_module(source_module)
                    result["nodes"].append({
                        "name": f"EXECUTE:{filename}",
                        "level": 0,
                        "status": "SUCCESS",
                    })
                except Exception as e:
                    result["nodes"].append({
                        "name": f"EXECUTE:{filename}",
                        "level": 0,
                        "status": f"FAILED: {e}",
                        "traceback": traceback.format_exc(),
                    })
        except Exception as e:
            result["nodes"].append({
                "name": filename,
                "level": 0,
                "status": f"spec_failed: {e}",
            })
        
        imports = self.extract_imports(file_path)
        print(f"Found {len(imports)} imports: {imports}")
        
        for imp in imports:
            print(f"\n  Testing: {imp}")
            if imp in sys.stdlib_module_names or imp.startswith('_'):
                print(f"    -> SKIP (stdlib)")
                continue
                
            success, error = self.test_import(imp)
            
            if success:
                result["success_count"] += 1
                print(f"    -> OK")
            else:
                result["failed_count"] += 1
                print(f"    -> FAILED: {error[:100] if error else 'unknown'}")
                result["nodes"].append({
                    "name": imp,
                    "level": 1,
                    "status": "FAILED",
                    "error": error,
                })
                
                self._investigate_failure(imp)
        
        new_modules = set(sys.modules.keys()) - original_modules
        result["added_modules"] = list(new_modules)
        
        return result
    
    def _investigate_failure(self, module_name: str):
        """Deep investigation of why a module import failed."""
        print(f"\n    Investigating failure of: {module_name}")
        
        parts = module_name.split('.')
        for i in range(1, len(parts) + 1):
            partial = '.'.join(parts[:i])
            print(f"      Testing partial: {partial}")
            success, _ = self.test_import(partial)
            if success:
                print(f"        -> {partial} OK")
            else:
                print(f"        -> {partial} FAILED")
        
        path = self.find_module_path(module_name)
        if path:
            print(f"      Found at: {path}")
            deps = self.extract_imports(path)
            print(f"      Its dependencies: {deps}")
            
            for dep in deps:
                if dep not in sys.stdlib_module_names and not dep.startswith('_'):
                    dpath = self.find_module_path(dep)
                    print(f"        Checking dep {dep}: ", end="")
                    success, _ = self.test_import(dep)
                    if success:
                        print("OK")
                    else:
                        print(f"FAILED -> {dpath}")

def find_import_error_in_chain(filename: str) -> Dict:
    """Find the exact import error in a file's import chain."""
    file_path = PROJECT_ROOT / filename
    
    if not file_path.exists():
        return {"error": f"File not found: {filename}"}
    
    result = {
        "file": filename,
        "import_error_found": False,
        "error_line": None,
        "error_module": None,
        "error_details": None,
        "import_chain": [],
    }
    
    print(f"\n{'#'*70}")
    print(f"# DETAILED TRACE: {filename}")
    print(f"{'#'*70}")
    
    original_modules = set(sys.modules.keys())
    modules_before = original_modules.copy()
    
    content = file_path.read_text()
    lines = content.split('\n')
    
    import_lines = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            import_lines.append((i, stripped))
    
    print(f"\nImport statements found at lines: {[l[0] for l in import_lines]}")
    
    for line_num, import_stmt in import_lines:
        print(f"\n  Line {line_num}: {import_stmt}")
        
        if 'from ' in import_stmt:
            parts = import_stmt.split()
            if len(parts) >= 2:
                module = parts[1].split('.')[0]
                if '(' not in module and module not in sys.stdlib_module_names:
                    success, error = test_import(module)
                    if not success:
                        result["import_error_found"] = True
                        result["error_line"] = line_num
                        result["error_module"] = module
                        result["error_details"] = error
                        print(f"    *** ERROR HERE ***")
                        print(f"    {error[:200]}")
    
    try:
        print(f"\n  Attempting full file execution...")
        spec = importlib.util.spec_from_file_location(f"__diag__", file_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print(f"    File executed successfully")
    except Exception as e:
        tb = traceback.format_exc()
        lines_tb = tb.split('\n')
        
        print(f"\n  *** EXECUTION FAILED ***")
        
        for i, tb_line in enumerate(lines_tb):
            if 'File "<string>"' in tb_line and 'line' in tb_line:
                result["import_error_found"] = True
                result["error_line"] = 10
                print(f"  -> {tb_line}")
                
        for line in lines_tb:
            if 'ModuleNotFoundError' in line or 'ImportError' in line:
                result["error_details"] = line.strip()
                print(f"  -> ERROR: {line.strip()}")
                
    modules_after = set(sys.modules.keys())
    result["new_modules"] = list(modules_after - modules_before)
    
    return result

def test_import(module_name: str) -> Tuple[bool, Optional[str]]:
    """Test a single import."""
    try:
        importlib.import_module(module_name)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def diagnose_shared_dependency_failures():
    """Identify which shared dependency causes import chain failures."""
    print(f"\n{'#'*70}")
    print(f"# SHARED DEPENDENCY ANALYSIS")
    print(f"{'#'*70}")
    
    all_imports = defaultdict(list)
    
    for filename in PROBLEM_FILES:
        file_path = PROJECT_ROOT / filename
        if not file_path.exists():
            continue
            
        tracer = ImportChainTracer(PROJECT_ROOT)
        imports = tracer.extract_imports(file_path)
        
        for imp in imports:
            if imp not in sys.stdlib_module_names:
                all_imports[imp].append(filename)
    
    print(f"\nImport frequency across problem files:")
    shared = []
    for imp, files in sorted(all_imports.items(), key=lambda x: -len(x[1])):
        print(f"  {imp}: {len(files)} files - {files}")
        if len(files) >= 2:
            shared.append(imp)
    
    print(f"\nShared dependencies (imported by 2+ files): {shared}")
    
    for imp in shared:
        print(f"\n  Testing shared dependency: {imp}")
        success, error = test_import(imp)
        if not success:
            print(f"    *** SHARED DEPENDENCY FAILED ***")
            print(f"    Error: {error}")
            print(f"    Files depending on this: {all_imports[imp]}")
    
    return shared

def check_circular_imports():
    """Check for circular import patterns."""
    print(f"\n{'#'*70}")
    print(f"# CIRCULAR IMPORT DETECTION")
    print(f"{'#'*70}")
    
    import_graph = defaultdict(set)
    
    for filename in PROBLEM_FILES:
        file_path = PROJECT_ROOT / filename
        if not file_path.exists():
            continue
            
        tracer = ImportChainTracer(PROJECT_ROOT)
        imports = tracer.extract_imports(file_path)
        
        for imp in imports:
            if imp not in sys.stdlib_module_names:
                import_graph[filename].add(imp)
                import_graph[imp].add(f"<-{filename}")
    
    def find_cycle(node, path, visited):
        if node in path:
            cycle_start = path.index(node)
            return path[cycle_start:] + [node]
        if node in visited:
            return None
            
        new_path = path + [node]
        new_visited = visited | {node}
        
        neighbors = import_graph.get(node, set())
        for neighbor in neighbors:
            if neighbor.startswith('<-'):
                continue
            cycle = find_cycle(neighbor, new_path, new_visited)
            if cycle:
                return cycle
        return None
    
    cycles = []
    for node in import_graph:
        if not node.startswith('<-'):
            cycle = find_cycle(node, [], set())
            if cycle:
                cycles.append(cycle)
    
    if cycles:
        print(f"Found {len(cycles)} potential circular import chains:")
        for cycle in cycles:
            print(f"  {' -> '.join(cycle)}")
    else:
        print("No circular import chains detected")

def run_full_diagnostic():
    """Run complete import chain diagnostic."""
    print("="*70)
    print("ZO-SENTINEL: Import Chain Failure Diagnostic v2")
    print("="*70)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Problem files: {PROBLEM_FILES}")
    print(f"Python version: {sys.version}")
    print(f"Current sys.path entries: {len(sys.path)}")
    
    results = {}
    
    for filename in PROBLEM_FILES:
        print(f"\n{'='*70}")
        print(f"ANALYZING: {filename}")
        print(f"{'='*70}")
        
        result = find_import_error_in_chain(filename)
        results[filename] = result
    
    print(f"\n{'='*70}")
    print("SHARED DEPENDENCY ANALYSIS")
    print(f"{'='*70}")
    shared = diagnose_shared_dependency_failures()
    
    print(f"\n{'='*70}")
    print("CIRCULAR IMPORT CHECK")
    print(f"{'='*70}")
    check_circular_imports()
    
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    
    for filename, result in results.items():
        print(f"\n{filename}:")
        if result.get("import_error_found"):
            print(f"  ERROR FOUND at line {result.get('error_line')}")
            print(f"  Module: {result.get('error_module')}")
            print(f"  Details: {result.get('error_details', 'N/A')[:200]}")
        else:
            print(f"  No import error detected in initial scan")
    
    print(f"\nShared dependencies causing issues: {shared}")
    
    return results

if __name__ == "__main__":
    run_full_diagnostic()