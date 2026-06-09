#!/usr/bin/env python3
"""
import_failure_root_cause_diagnostic.py

Diagnostic-only module to trace root cause of importlib errors occurring at line 10
in three files (registry_api.py, rug_pull_monitor.py, signal_analyser.py) that exhibit
identical traceback patterns indicating a broken shared import dependency.
"""

import sys
import os
import json
import ast
import traceback
import importlib.util
import importlib.machinery
import builtins
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path


class ImportInterceptor:
    """Intercepts and logs all import attempts to trace failures."""
    
    def __init__(self) -> None:
        self.log: List[Dict[str, Any]] = []
        self.original_import = builtins.__import__
        self._active = False
    
    def activate(self) -> None:
        """Activate the import interceptor."""
        if not self._active:
            self._active = True
            builtins.__import__ = self._wrapped_import
    
    def deactivate(self) -> None:
        """Deactivate the import interceptor and restore original import."""
        if self._active:
            self._active = False
            builtins.__import__ = self.original_import
    
    def _wrapped_import(
        self,
        name: str,
        globals: Optional[Dict] = None,
        locals: Optional[Dict] = None,
        fromlist: tuple = (),
        level: int = 0
    ):
        """Wrapper around __import__ that logs all import attempts."""
        attempted_path = "unknown"
        result = "success"
        error_type = ""
        error_message = ""
        
        # Determine the attempted path
        if name:
            try:
                spec = importlib.util.find_spec(name, level=level)
                if spec is not None and spec.origin:
                    attempted_path = spec.origin
            except (ValueError, ModuleNotFoundError):
                attempted_path = f"not found in sys.path (level={level})"
        
        try:
            return self.original_import(name, globals, locals, fromlist, level)
        except ModuleNotFoundError as e:
            result = "failure"
            error_type = "ModuleNotFoundError"
            error_message = str(e)
        except ImportError as e:
            result = "failure"
            error_type = "ImportError"
            error_message = str(e)
        except AttributeError as e:
            result = "failure"
            error_type = "AttributeError"
            error_message = str(e)
        except Exception as e:
            result = "failure"
            error_type = type(e).__name__
            error_message = str(e)
        
        self.log.append({
            "module_name": name,
            "attempted_path": attempted_path,
            "result": result,
            "error_type": error_type,
            "error_message": error_message
        })
        
        raise
    
    def get_log(self) -> List[Dict[str, Any]]:
        """Return the import log."""
        return self.log.copy()
    
    def clear(self) -> None:
        """Clear the import log."""
        self.log.clear()


# Global interceptor instance
import_interceptor = ImportInterceptor()


def _probe_known_modules() -> List[Dict[str, Any]]:
    """Probe known module imports for diagnostic baseline."""
    results = []
    
    known_modules = ["os", "sys", "json", "re", "collections"]
    
    import_interceptor.activate()
    try:
        for mod_name in known_modules:
            try:
                module = importlib.import_module(mod_name)
                results.append({
                    "module_name": mod_name,
                    "attempted_path": getattr(module, "__file__", "built-in/namespace"),
                    "result": "success",
                    "error_type": "",
                    "error_message": ""
                })
            except Exception as e:
                results.append({
                    "module_name": mod_name,
                    "attempted_path": "import failed",
                    "result": "failure",
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                })
    finally:
        import_interceptor.deactivate()
    
    return results


def _analyze_sys_path() -> Dict[str, Any]:
    """
    Enumerate all sys.path entries with existence/readability checks.
    Identify missing or inaccessible directories.
    """
    path_entries = []
    problematic_entries = []
    
    for entry in sys.path:
        entry_exists = os.path.exists(entry)
        entry_readable = (
            os.access(entry, os.R_OK) if entry_exists else False
        )
        
        path_entries.append([entry, entry_exists, entry_readable])
        
        if not entry_exists:
            problematic_entries.append({
                "path": entry,
                "reason": "Path does not exist"
            })
        elif not entry_readable:
            problematic_entries.append({
                "path": entry,
                "reason": "Path is not readable"
            })
        elif not os.path.isdir(entry):
            problematic_entries.append({
                "path": entry,
                "reason": "Path is not a directory"
            })
    
    # Check for empty entries
    if "" in sys.path:
        problematic_entries.append({
            "path": "",
            "reason": "Empty string entry (current directory)"
        })
    
    return {
        "entries": path_entries,
        "problematic_entries": problematic_entries,
        "analysis_complete": True
    }


def _inspect_importlib_machinery() -> Dict[str, Any]:
    """
    Inspect importlib machinery and probe target modules.
    Use importlib.util.find_spec to check module availability.
    """
    # Gather information about available finders
    finders_info = []
    for finder in sys.meta_path:
        finder_info = {
            "finder_class": f"{finder.__class__.__module__}.{finder.__class__.__name__}",
            "has_find_module": hasattr(finder, 'find_module'),
            "has_find_spec": hasattr(finder, 'find_spec')
        }
        finders_info.append(finder_info)
    
    # Inspect importlib.machinery
    machinery_info = {
        "builtin_importers": [imp.__name__ for imp in importlib.machinery._builtin_importers],
        "extension_suffixes": importlib.machinery.EXTENSION_SUFFIXES[:3],
        "source_suffixes": importlib.machinery.SOURCE_SUFFIXES[:3],
        "all_suffixes_count": (
            len(importlib.machinery.EXTENSION_SUFFIXES) +
            len(importlib.machinery.SOURCE_SUFFIXES) +
            len(importlib.machinery.BYTECODE_SUFFIXES)
        )
    }
    
    # Probe the three target modules
    target_modules = [
        "registry_api",
        "rug_pull_monitor",
        "signal_analyser"
    ]
    
    module_probe_results = []
    probe_errors = []
    
    for target in target_modules:
        spec = importlib.util.find_spec(target)
        
        if spec is not None:
            module_probe_results.append({
                "module_name": target,
                "found": True,
                "spec_info": {
                    "name": spec.name,
                    "origin": spec.origin,
                    "has_loader": spec.loader is not None,
                    "loader_type": (
                        f"{spec.loader.__class__.__module__}.{spec.loader.__class__.__name__}"
                        if spec.loader else None
                    )
                }
            })
        else:
            module_probe_results.append({
                "module_name": target,
                "found": False,
                "spec_info": None
            })
            
            # Attempt direct import to capture the error
            try:
                importlib.import_module(target)
            except Exception as e:
                tb = traceback.format_exc()
                probe_errors.append({
                    "module_name": target,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback_excerpt": "\n".join(tb.split("\n")[-5:])
                })
    
    return {
        "available_finders": finders_info,
        "machinery_details": machinery_info,
        "target_probe_results": module_probe_results,
        "probe_errors": probe_errors
    }


def _analyze_shared_dependency_chain(
    import_log: List[Dict[str, Any]],
    machinery_results: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract imported modules from line 10 traceback of each target file.
    Identify the shared broken module(s) and build dependency chain.
    """
    target_modules = [
        ("registry_api.py", "registry_api"),
        ("rug_pull_monitor.py", "rug_pull_monitor"),
        ("signal_analyser.py", "signal_analyser")
    ]
    
    line_10_imports = []
    chains = []
    
    for file_name, module_name in target_modules:
        # Try to read source to get line 10
        source_code = None
        line_10_content = None
        
        # Search for the file in sys.path
        for path_entry in sys.path:
            potential_path = os.path.join(path_entry, file_name)
            if os.path.isfile(potential_path):
                try:
                    with open(potential_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        if len(lines) >= 10:
                            source_code = "".join(lines)
                            line_10_content = lines[9].strip()
                except Exception:
                    pass
                break
        
        if line_10_content:
            line_10_imports.append({
                "file": file_name,
                "line_10": line_10_content
            })
            
            # Parse to extract import
            imported_modules = _extract_imports_from_line(line_10_content)
            chains.append({
                "file": file_name,
                "line_10_content": line_10_content,
                "imported_modules": imported_modules
            })
    
    # Identify shared imports
    all_imports = []
    for chain in chains:
        all_imports.extend(chain.get("imported_modules", []))
    
    import_counts: Dict[str, int] = {}
    for imp in all_imports:
        import_counts[imp] = import_counts.get(imp, 0) + 1
    
    # Common imports are those appearing in all three files
    common_imports = [
        imp for imp, count in import_counts.items()
        if count == len(target_modules)
    ]
    
    # Build chain from all extracted imports
    dependency_chain = []
    for chain in chains:
        file_name = chain["file"]
        for imported in chain.get("imported_modules", []):
            dependency_chain.append([file_name, imported])
    
    return {
        "common_imports": common_imports,
        "depth": 1 if common_imports else 0,
        "chain": dependency_chain
    }


def _extract_imports_from_line(line: str) -> List[str]:
    """Extract module name from an import statement line."""
    line = line.strip()
    
    if line.startswith("import "):
        # Handle: import module_name
        module_part = line[7:].strip()
        # Handle: import a, b, c
        first_module = module_part.split(",")[0].strip()
        # Handle: import module as alias
        if " as " in first_module:
            first_module = first_module.split(" as ")[0].strip()
        return [first_module]
    
    elif line.startswith("from "):
        # Handle: from module import name
        parts = line.split()
        if len(parts) >= 2:
            module = parts[1]
            return [module]
    
    return []


def _identify_root_cause(
    import_log: List[Dict[str, Any]],
    machinery_results: Dict[str, Any],
    dependency_chain: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Classify error and identify root cause.
    Returns identified_root_cause with module name, reason, and evidence.
    """
    target_modules = [
        ("registry_api.py", "registry_api"),
        ("rug_pull_monitor.py", "rug_pull_monitor"),
        ("signal_analyser.py", "signal_analyser")
    ]
    
    root_cause = {
        "module": "unknown",
        "reason": "Unable to determine specific root cause",
        "evidence": {},
        "line_number": 10
    }
    
    # Collect all import errors from interceptor log
    failed_imports = [
        entry for entry in import_log
        if entry.get("result") == "failure"
    ]
    
    # Check for common patterns
    common_imports = dependency_chain.get("common_imports", [])
    line_10_imports = []
    
    for file_name, module_name in target_modules:
        for path_entry in sys.path:
            potential_path = os.path.join(path_entry, file_name)
            if os.path.isfile(potential_path):
                try:
                    with open(potential_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        if len(lines) >= 10:
                            line_10 = lines[9].strip()
                            imported = _extract_imports_from_line(line_10)
                            if imported:
                                line_10_imports.append({
                                    "file": file_name,
                                    "line_10": line_10,
                                    "imported": imported[0]
                                })
                except Exception:
                    pass
                break
    
    # Check if all files have the same line 10 import
    if len(line_10_imports) >= 2:
        imports_set = [item["imported"] for item in line_10_imports]
        if len(set(imports_set)) == 1:
            shared_module = imports_set[0]