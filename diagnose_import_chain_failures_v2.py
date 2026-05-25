#!/usr/bin/env python3
"""
diagnose_import_chain_failures_v2.py -- ZO-SENTINEL
Diagnostic tool for import chain failures in registry_api.py, rug_pull_monitor.py, signal_analyser.py.
Reports: (a) shared dependency chain broken, (b) first-failing import per file, (c) recommended fixes.
DO NOT auto-patch -- diagnostic only.
"""
import sys
import os
import traceback
import importlib
import importlib.util
import inspect
import ast
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, List, Set, Tuple, Any

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
TARGET_FILES = [
    "registry_api.py",
    "rug_pull_monitor.py",
    "signal_analyser.py",
]

OUTPUT_FILE = "/tmp/diagnose_import_chain_v2_results.txt"


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
    
    def try_import(self, module_name: str, level: int = 0) -> Tuple[bool, Optional[str]]:
        try:
            if level == 0:
                importlib.import_module(module_name)
            else:
                pass
            return True, None
        except ImportError as e:
            return False, str(e)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    
    def get_imports_from_source(self, source_path: Path) -> List[Tuple[str, int, Optional[str]]]:
        imports = []
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                content = f.read()
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append((alias.name, 0, alias.asname))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    level = node.level
                    for alias in node.names:
                        full_name = f"{module}.{alias.name}" if module else alias.name
                        imports.append((full_name, level, alias.asname))
        except Exception as e:
            pass
        return imports
    
    def trace_import_chain(self, target_file: str) -> Dict[str, Any]:
        source_path = self.project_root / target_file
        if not source_path.exists():
            return {"error": f"File not found: {source_path}", "imports": []}
        
        results = {
            "file": target_file,
            "path": str(source_path),
            "imports": [],
            "failures": [],
            "shared_dependencies": defaultdict(list),
        }
        
        direct_imports = self.get_imports_from_source(source_path)
        
        for import_name, level, alias in direct_imports:
            import_info = {
                "name": import_name,
                "alias": alias,
                "level": level,
                "status": "pending",
                "error": None,
                "children": [],
                "first_failure": None,
            }
            
            if import_name.startswith('_'):
                import_info["status"] = "skipped_private"
                results["imports"].append(import_info)
                continue
            
            if level > 0:
                import_info["status"] = "relative_skip"
                results["imports"].append(import_info)
                continue
            
            success, error = self.try_import(import_name)
            
            if success:
                import_info["status"] = "success"
                self.successful_imports.add(import_name)
            else:
                import_info["status"] = "failed"
                import_info["error"] = error
                self.failed_imports.add(import_name)
                import_info["first_failure"] = import_name
                results["failures"].append(import_info)
                
                children = self._find_child_imports(import_name)
                for child in children:
                    results["shared_dependencies"][child].append({
                        "parent": import_name,
                        "file": target_file,
                    })
            
            results["imports"].append(import_info)
        
        return results
    
    def _find_child_imports(self, module_name: str) -> List[str]:
        try:
            spec = importlib.util.find_spec(module_name)
            if spec and spec.submodule_search_locations:
                for loc in spec.submodule_search_locations:
                    loc_path = Path(loc)
                    if loc_path.exists():
                        for py_file in loc_path.glob("*.py"):
                            if py_file.name.startswith("_"):
                                continue
                            child_imports = self.get_imports_from_source(py_file)
                            for child_name, _, _ in child_imports:
                                if not child_name.startswith("_"):
                                    yield child_name
        except Exception:
            pass


class SharedDependencyAnalyzer:
    def __init__(self):
        self.all_failures: Dict[str, List[str]] = defaultdict(list)
        self.shared_issues: Dict[str, List[str]] = {}
        
    def add_file_failures(self, file_name: str, failures: List[Dict]):
        for failure in failures:
            import_name = failure["name"]
            self.all_failures[import_name].append(file_name)
    
    def find_shared_dependencies(self) -> Dict[str, List[str]]:
        for import_name, files in self.all_failures.items():
            if len(files) > 1:
                self.shared_issues[import_name] = files
        return self.shared_issues
    
    def compute_dependency_distance(self, target: str, all_imports: List[str]) -> int:
        return 1 if target in all_imports else 0


class DiagnosticReport:
    def __init__(self):
        self.lines: List[str] = []
        self.timestamp = None
        
    def add_line(self, line: str = ""):
        self.lines.append(line)
    
    def add_header(self, text: str, char: str = "="):
        self.add_line()
        self.add_line(char * 70)
        self.add_line(text)
        self.add_line(char * 70)
        self.add_line()
    
    def add_subsection(self, text: str):
        self.add_line()
        self.add_line(f"--- {text} ---")
        self.add_line()
    
    def add_code_block(self, lines: List[str]):
        for line in lines:
            self.add_line(f"  {line}")
        self.add_line()
    
    def add_list(self, items: List[str], numbered: bool = False):
        for i, item in enumerate(items, 1 if numbered else 0):
            prefix = f"{i}. " if numbered else "  - "
            self.add_line(f"{prefix}{item}")
        self.add_line()
    
    def format_failure_info(self, failure: Dict) -> List[str]:
        lines = []
        lines.append(f"Import: {failure['name']}")
        if failure.get('alias'):
            lines.append(f"  Alias: {failure['alias']}")
        lines.append(f"  Status: {failure['status']}")
        if failure.get('error'):
            lines.append(f"  Error: {failure['error']}")
        if failure.get('first_failure'):
            lines.append(f"  First Failure Point: {failure['first_failure']}")
        return lines
    
    def generate(self) -> str:
        return "\n".join(self.lines)
    
    def save(self, path: str):
        with open(path, 'w') as f:
            f.write(self.generate())


def run_diagnostic() -> Dict[str, Any]:
    report = DiagnosticReport()
    tracer = ImportChainTracer(PROJECT_ROOT)
    analyzer = SharedDependencyAnalyzer()
    
    report.add_header("ZO-SENTINEL IMPORT CHAIN DIAGNOSTIC REPORT")
    report.add_line(f"Project Root: {PROJECT_ROOT}")
    report.add_line(f"Timestamp: Generated by diagnostic tool")
    report.add_line()
    report.add_line(f"Target Files: {', '.join(TARGET_FILES)}")
    
    file_results = {}
    
    for target_file in TARGET_FILES:
        report.add_subsection(f"ANALYZING: {target_file}")
        
        result = tracer.trace_import_chain(target_file)
        file_results[target_file] = result
        
        if "error" in result:
            report.add_line(f"ERROR: {result['error']}")
            continue
        
        report.add_line(f"File: {result['path']}")
        report.add_line()
        
        report.add_line(f"Direct Imports Found: {len(result['imports'])}")
        report.add_line(f"Failures: {len(result['failures'])}")
        
        if result['failures']:
            report.add_subsection("FAILED IMPORTS")
            for failure in result['failures']:
                report.add_code_block(report.format_failure_info(failure))
        else:
            report.add_line("  No import failures detected.")
        
        analyzer.add_file_failures(target_file, result['failures'])
    
    report.add_subsection("SHARED DEPENDENCY ANALYSIS")
    
    shared_deps = analyzer.find_shared_dependencies()
    
    if shared_deps:
        report.add_line("SHARED BROKEN DEPENDENCIES (fail in multiple files):")
        report.add_line()
        for dep, files in sorted(shared_deps.items()):
            report.add_line(f"  {dep}")
            report.add_line(f"    Fails in: {', '.join(files)}")
            
            spec = importlib.util.find_spec(dep)
            if spec:
                report.add_line(f"    Spec found: {spec.origin}")
                if spec.submodule_search_locations:
                    report.add_line(f"    Locations: {spec.submodule_search_locations}")
            else:
                report.add_line("    Spec NOT found - module not installed")
            report.add_line()
    else:
        report.add_line("  No shared broken dependencies detected across all target files.")
    
    report.add_subsection("FIRST-FAILING IMPORT PER FILE")
    
    for target_file in TARGET_FILES:
        result = file_results.get(target_file, {})
        if 'failures' in result and result['failures']:
            first_failure = result['failures'][0]
            report.add_line(f"  {target_file}:")
            report.add_line(f"    First failure: {first_failure['name']}")
            report.add_line(f"    Error: {first_failure.get('error', 'Unknown')}")
            
            for child in first_failure.get('children', []):
                report.add_line(f"      Child import: {child}")
            report.add_line()
        else:
            report.add_line(f"  {target_file}: No failures detected")
    
    report.add_subsection("RECOMMENDED FIXES BY FILE")
    
    recommendations = generate_recommendations(tracer, file_results, shared_deps)
    
    for file_name, recs in recommendations.items():
        report.add_line(f"  {file_name}:")
        for i, rec in enumerate(recs, 1):
            report.add_line(f"    {i}. {rec}")
        report.add_line()
    
    report.add_subsection("ROOT CAUSE ANALYSIS")
    
    root_causes = identify_root_causes(tracer, file_results, shared_deps)
    for cause in root_causes:
        report.add_line(f"  - {cause}")
    
    report.add_line()
    report.add_subsection("CIRCULAR DEPENDENCY CHECK")
    
    circular_deps = detect_circular_dependencies(tracer, TARGET_FILES)
    if circular_deps:
        report.add_line("Potential circular dependencies found:")
        for cycle in circular_deps:
            report.add_line(f"  {' -> '.join(cycle)}")
    else:
        report.add_line("  No circular dependencies detected.")
    
    report.add_line()
    report.add_subsection("SUMMARY")
    
    total_failures = sum(len(r.get('failures', [])) for r in file_results.values())
    report.add_line(f"  Total files analyzed: {len(TARGET_FILES)}")
    report.add_line(f"  Total import failures: {total_failures}")
    report.add_line(f"  Shared broken dependencies: {len(shared_deps)}")
    
    if shared_deps:
        report.add_line()
        report.add_line("ACTION REQUIRED:")
        report.add_line("  Install missing shared dependencies. Check pip packages and")
        report.add_line("  ensure all transitive dependencies are properly installed.")
    
    report_text = report.generate()
    print(report_text)
    
    report.save(OUTPUT_FILE)
    print(f"\nFull report saved to: {OUTPUT_FILE}")
    
    return {
        "file_results": file_results,
        "shared_dependencies": shared_deps,
        "recommendations": recommendations,
        "root_causes": root_causes,
        "report_path": OUTPUT_FILE,
    }


def generate_recommendations(tracer: ImportChainTracer, file_results: Dict, shared_deps: Dict) -> Dict[str, List[str]]:
    recommendations = {}
    
    for target_file in TARGET_FILES:
        result = file_results.get(target_file, {})
        recs = []
        
        failures = result.get('failures', [])
        
        if not failures:
            recs.append("No import issues detected - file may have runtime issues instead")
            recommendations[target_file] = recs
            continue
        
        failed_imports = [f['name'] for f in failures]
        
        for dep_name, files in shared_deps.items():
            if target_file in files:
                recs.append(f"Shared dependency '{dep_name}' fails in {len(files)} files. "
                          f"Run: pip install {dep_name.split('.')[0]} or check environment.")
        
        for failure in failures:
            import_name = failure['name']
            
            if 'No module named' in failure.get('error', ''):
                module = import_name.split('.')[0]
                recs.append(f"Missing module '{module}'. Add to requirements.txt and install.")
            
            if 'cannot import name' in failure.get('error', ''):
                recs.append(f"Circular or missing import in '{import_name}'. "
                          f"Check import order or version compatibility.")
            
            if 'ImportError' in failure.get('error', ''):
                recs.append(f"Import error in '{import_name}'. Verify module is in PYTHONPATH.")
        
        local_fails = [f for f in failures if f['name'] not in shared_deps]
        if local_fails:
            recs.append(f"File-specific failures: {[f['name'] for f in local_fails]}. "
                       f"Check if these modules are needed or can be conditionally imported.")
        
        if not recs:
            recs.append("Investigate import chain manually - root cause unclear from static analysis.")
        
        recommendations[target_file] = recs
    
    return recommendations


def identify_root_causes(tracer: ImportChainTracer, file_results: Dict, shared_deps: Dict) -> List[str]:
    causes = []
    
    if shared_deps:
        most_common = max(shared_deps.items(), key=lambda x: len(x[1]))
        causes.append(f"Primary root cause: Shared dependency '{most_common[0]}' fails "
                     f"in {len(most_common[1])} files ({', '.join(most_common[1])}). "
                     f"This is likely a missing or version-incompatible package.")
        
        causes.append(f"Fix the shared dependency first - other files will likely import successfully after.")
    
    for target_file in TARGET_FILES:
        result = file_results.get(target_file, {})
        failures = result.get('failures', [])
        
        if failures:
            first_error = failures[0].get('error', '')
            
            if 'known_threats' in str(failures):
                causes.append(f"{target_file}: Appears to import 'known_threats' module which may not exist.")
            
            if 'duckdb' in first_error.lower():
                causes.append(f"{target_file}: DuckDB import issue detected. "
                             f"Note: project rules forbid direct duckdb import - use write_service HTTP API.")
            
            if 'known_threats' in first_error or 'from known_threats' in first_error:
                causes.append(f"{target_file}: References 'known_threats' but this module may be missing or misnamed.")
    
    if not causes:
        causes.append("Unable to determine root cause from static analysis. "
                     "Likely runtime environment issue or missing sys.path configuration.")
    
    return causes


def detect_circular_dependencies(tracer: ImportChainTracer, target_files: List[str]) -> List[List[str]]:
    circular_deps = []
    visited = set()
    path = []
    
    def dfs(module_name: str, path: List[str]) -> Optional[List[str]]:
        if module_name in path:
            cycle_start = path.index(module_name)
            return path[cycle_start:] + [module_name]
        
        if module_name in visited:
            return None
        
        visited.add(module_name)
        path.append(module_name)
        
        visited.discard(module_name)
        path.pop()
        
        return None
    
    for target in target_files:
        source_path = PROJECT_ROOT / target
        if source_path.exists():
            imports = tracer.get_imports_from_source(source_path)
            for import_name, level, _ in imports:
                if level == 0:
                    cycle = dfs(import_name, [target])
                    if cycle:
                        circular_deps.append(cycle)
    
    return circular_deps


def main():
    print("=" * 70)
    print("ZO-SENTINEL IMPORT CHAIN DIAGNOSTIC TOOL v2")
    print("=" * 70)
    print()
    print(f"Analyzing: {', '.join(TARGET_FILES)}")
    print(f"Project root: {PROJECT_ROOT}")
    print()
    
    try:
        result = run_diagnostic()
        
        print()
        print("=" * 70)
        print("DIAGNOSTIC COMPLETE")
        print("=" * 70)
        
        shared = result.get('shared_dependencies', {})
        if shared:
            print(f"\nSHARED BROKEN DEPENDENCIES FOUND: {len(shared)}")
            for dep, files in shared.items():
                print(f"  - {dep}: fails in {len(files)} files")
        
        recommendations = result.get('recommendations', {})
        total_recs = sum(len(recs) for recs in recommendations.values())
        print(f"\nTOTAL RECOMMENDATIONS: {total_recs}")
        
        return 0
        
    except Exception as e:
        print(f"FATAL ERROR during diagnosis: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())