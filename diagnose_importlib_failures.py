import os
import sys
import json
import subprocess
from datetime import datetime, timedelta

PROJECT_ROOT = "/home/workspace/zo_sentinel"
SMOKE_LOG = os.path.join(PROJECT_ROOT, "logs", "smoke_test.log")
PYTHON_VERSION = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

PROTECTED_FILES = [
    "registry_api.py",
    "rug_pull_monitor.py", 
    "signal_analyser.py"
]

def get_utc_now():
    return datetime.utcnow()

def log(msg):
    ts = get_utc_now().isoformat()
    print(f"[{ts}] {msg}", flush=True)

def check_pycache_corruption(module_name):
    """Check for __pycache__ corruption issues"""
    results = {}
    
    # Check if __pycache__ exists
    cache_dir = os.path.join(PROJECT_ROOT, "__pycache__")
    if os.path.exists(cache_dir):
        results["cache_exists"] = True
        results["cache_path"] = cache_dir
        
        # Count .pyc files
        pyc_files = []
        for root, dirs, files in os.walk(cache_dir):
            for f in files:
                if f.endswith(".pyc"):
                    pyc_files.append(os.path.join(root, f))
        results["pyc_file_count"] = len(pyc_files)
        results["pyc_files"] = pyc_files[:10]  # Sample
        
        # Check for any .pyc files with mismatched versions
        bad_pyc = []
        for pf in pyc_files:
            base = pf[:-4]  # Remove .pyc
            # Check naming pattern: module.hash.pyc
            if "." in os.path.basename(pf):
                parts = os.path.basename(pf).split(".")
                if len(parts) >= 3:
                    pyc_version = parts[-2]
                    expected_version = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
                    if not pyc_version.startswith(expected_version):
                        bad_pyc.append({"file": pf, "version": pyc_version, "expected": expected_version})
        results["version_mismatch"] = len(bad_pyc) > 0
        results["bad_pyc_files"] = bad_pyc[:5]
    else:
        results["cache_exists"] = False
    
    return results

def check_python_version_consistency():
    """Check if Python version is consistent"""
    try:
        result = subprocess.run(
            ["python3", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return {
            "version_string": result.stdout.strip() if result.returncode == 0 else "unknown",
            "sys.version_info": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        }
    except Exception as e:
        return {"error": str(e)}

def check_sys_path_integrity():
    """Check sys.path for module resolution"""
    paths = sys.path[:]
    return {
        "path_count": len(paths),
        "paths": paths,
        "has_zo_sentinel": any("zo_sentinel" in p for p in paths),
        "has_workspace": any("workspace" in p for p in paths)
    }

def run_subprocess_import(module_name):
    """Try importing module in a fresh subprocess to isolate the issue"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name.replace('.py', '')}; print('OK')"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "0"}
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip()[:2000],
            "stderr_lines": result.stderr.strip().split("\n")[-20:]  # Last 20 lines
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}

def check_smoke_logs_for_module(module_name):
    """Parse smoke test logs for specific module failures"""
    if not os.path.exists(SMOKE_LOG):
        return {"log_exists": False}
    
    findings = {
        "log_exists": True,
        "importlib_errors": [],
        "line_10_errors": []
    }
    
    try:
        with open(SMOKE_LOG, "r") as f:
            content = f.read()
            lines = content.split("\n")
        
        in_module_section = False
        for i, line in enumerate(lines):
            if module_name in line and ("Testing" in line or "module" in line.lower()):
                in_module_section = True
                start_idx = i
            elif in_module_section and (line.startswith("=") or (module_name not in line and len(line.strip()) > 0)):
                in_module_section = False
            
            if in_module_section or (module_name in line and any(x in line for x in ["Error", "Failed", "ImportError", "ModuleNotFoundError"])):
                if "frozen importlib" in line or "<frozen" in line:
                    findings["importlib_errors"].append({
                        "line_num": i + 1,
                        "content": line.strip(),
                        "context": lines[max(0, i-3):min(len(lines), i+5)]
                    })
                if "line 10" in line or (i > 0 and "line 10" in lines[i-1]):
                    findings["line_10_errors"].append({
                        "line_num": i + 1,
                        "content": line.strip()
                    })
    except Exception as e:
        findings["parse_error"] = str(e)
    
    return findings

def check_module_source_integrity(module_name):
    """Check if module source file is intact"""
    module_path = os.path.join(PROJECT_ROOT, module_name)
    if not os.path.exists(module_path):
        return {"exists": False}
    
    try:
        # Read first 15 lines to see imports
        with open(module_path, "r") as f:
            lines = f.readlines()[:15]
        
        return {
            "exists": True,
            "line_count": len(open(module_path).readlines()),
            "first_15_lines": "".join(lines),
            "has_syntax_errors": False
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}

def check_import_chain(module_name):
    """Trace what the module imports at line 10"""
    module_path = os.path.join(PROJECT_ROOT, module_name)
    if not os.path.exists(module_path):
        return {"exists": False}
    
    try:
        with open(module_path, "r") as f:
            lines = f.readlines()
        
        # Line 10 is index 9
        if len(lines) >= 10:
            line_10 = lines[9].strip()
            line_11 = lines[10].strip() if len(lines) > 10 else None
            
            imports_at_10 = []
            for i, line in enumerate(lines[:15], 1):
                if line.strip().startswith(("import ", "from ")):
                    imports_at_10.append({"line": i, "content": line.strip()})
            
            return {
                "total_lines": len(lines),
                "line_10_content": line_10,
                "line_11_content": line_11,
                "imports_in_first_15": imports_at_10
            }
    except Exception as e:
        return {"error": str(e)}

def diagnose_file(module_name):
    """Full diagnostic for a single module"""
    log(f"\n{'='*60}")
    log(f"DIAGNOSING: {module_name}")
    log(f"{'='*60}")
    
    diagnosis = {
        "module": module_name,
        "timestamp": get_utc_now().isoformat(),
        "checks": {}
    }
    
    # Check __pycache__ for this specific module
    module_cache = os.path.join(PROJECT_ROOT, "__pycache__", f"{module_name.replace('.py', '')}.cpython-*")
    diagnosis["checks"]["pycache"] = check_pycache_corruption(module_name)
    
    # Check source integrity
    diagnosis["checks"]["source"] = check_module_source_integrity(module_name)
    
    # Check import chain
    diagnosis["checks"]["import_chain"] = check_import_chain(module_name)
    
    # Try fresh import in subprocess
    diagnosis["checks"]["fresh_import"] = run_subprocess_import(module_name)
    
    # Check smoke logs
    diagnosis["checks"]["smoke_logs"] = check_smoke_logs_for_module(module_name)
    
    # Sys.path check
    diagnosis["checks"]["sys_path"] = check_sys_path_integrity()
    
    return diagnosis

def format_diagnosis(diag):
    """Format diagnosis into readable output"""
    output = []
    output.append(f"\nModule: {diag['module']}")
    output.append(f"Timestamp: {diag['timestamp']}")
    
    checks = diag['checks']
    
    # Source check
    if "source" in checks:
        src = checks["source"]
        if src.get("exists"):
            output.append(f"  Source: OK ({src.get('line_count', '?')} lines)")
            output.append(f"  First 15 lines:\n    {src.get('first_15_lines', '')[:500]}")
        else:
            output.append("  Source: MISSING")
    
    # Import chain
    if "import_chain" in checks:
        ic = checks["import_chain"]
        output.append(f"\n  Import chain analysis:")
        output.append(f"    Total lines: {ic.get('total_lines', '?')}")
        if "imports_in_first_15" in ic:
            for imp in ic.get("imports_in_first_15", []):
                output.append(f"    Line {imp['line']}: {imp['content']}")
    
    # Fresh import result
    if "fresh_import" in checks:
        fi = checks["fresh_import"]
        if "error" in fi:
            output.append(f"\n  Fresh import: ERROR - {fi['error']}")
        else:
            status = "OK" if fi.get("returncode") == 0 else "FAILED"
            output.append(f"\n  Fresh import: {status}")
            if fi.get("stderr"):
                output.append(f"    stderr: {fi['stderr'][:500]}")
    
    # Smoke log errors
    if "smoke_logs" in checks:
        sl = checks["smoke_logs"]
        if sl.get("importlib_errors"):
            output.append(f"\n  Importlib errors in smoke log: {len(sl['importlib_errors'])}")
            for err in sl["importlib_errors"][:3]:
                output.append(f"    Line {err['line_num']}: {err['content'][:100]}")
                output.append(f"    Context: {err.get('context', [])[:3]}")
        if sl.get("line_10_errors"):
            output.append(f"\n  Line 10 errors: {len(sl['line_10_errors'])}")
    
    return "\n".join(output)

def main():
    log("=" * 70)
    log("DIAGNOSTIC: Importlib Failures Analysis")
    log(f"Python: {PYTHON_VERSION}")
    log(f"Project: {PROJECT_ROOT}")
    log("=" * 70)
    
    all_diagnoses = []
    
    for module in PROTECTED_FILES:
        if os.path.exists(os.path.join(PROJECT_ROOT, module)):
            diag = diagnose_file(module)
            all_diagnoses.append(diag)
            print(format_diagnosis(diag))
        else:
            log(f"Module not found: {module}")
    
    # Cross-module analysis
    log("\n" + "=" * 60)
    log("CROSS-MODULE ANALYSIS")
    log("=" * 60)
    
    common_issues = {
        "shared_cache_corruption": False,
        "python_version_mismatch": False,
        "sys_path_problem": False,
        "circular_import": False,
        "missing_dependency": []
    }
    
    # Check for shared cache issue
    cache_dir = os.path.join(PROJECT_ROOT, "__pycache__")
    if os.path.exists(cache_dir):
        pyc_count = len([f for f in os.listdir(cache_dir) if f.endswith(".pyc")])
        log(f"  Total .pyc files in __pycache__: {pyc_count}")
        if pyc_count == 0:
            common_issues["shared_cache_corruption"] = "No .pyc files - cache may be empty or corrupted"
    
    # Check for version mismatch
    for diag in all_diagnoses:
        if "pycache" in diag["checks"]:
            if diag["checks"]["pycache"].get("version_mismatch"):
                common_issues["python_version_mismatch"] = True
                log("  Found version-mismatched .pyc files")
                break
    
    # All modules same line 10?
    line_10_contents = []
    for diag in all_diagnoses:
        ic = diag["checks"].get("import_chain", {})
        if "line_10_content" in ic:
            line_10_contents.append(ic["line_10_content"])
    
    if len(set(line_10_contents)) == 1 and line_10_contents:
        log(f"  ALL modules have identical line 10: {line_10_contents[0]}")
        if "import" in line_10_contents[0]:
            common_issues["circular_import"] = True
            log("  -> Likely circular import or shared import at line 10")
    
    # Diagnosis summary
    log("\n" + "=" * 60)
    log("DIAGNOSIS SUMMARY")
    log("=" * 60)
    
    findings = []
    
    if common_issues["shared_cache_corruption"]:
        findings.append(("CRITICAL", "Shared __pycache__ corruption detected"))
    
    if common_issues["python_version_mismatch"]:
        findings.append(("CRITICAL", "Python version mismatch in bytecode"))
    
    if common_issues["circular_import"]:
        findings.append(("HIGH", "Circular import pattern at line 10"))
    
    if common_issues["sys_path_problem"]:
        findings.append(("MEDIUM", "sys.path resolution issue"))
    
    for severity, msg in findings:
        log(f"  [{severity}] {msg}")
    
    # Remediation steps
    log("\n" + "=" * 60)
    log("REMEDIATION STEPS")
    log("=" * 60)
    
    if common_issues["circular_import"]:
        log("  1. Clear __pycache__: rm -rf __pycache__/*.pyc")
        log("  2. Check circular imports in shared dependencies")
        log("  3. Consider deferred imports pattern")
        log("  4. Verify db_utils.py doesn't create circular deps")
    
    if common_issues["shared_cache_corruption"]:
        log("  1. Clear entire __pycache__: find . -type d -name __pycache__ -exec rm -rf {} +")
        log("  2. Restart Python processes to rebuild cache")
        log("  3. Verify Python version consistency")
    
    # Save full report
    report_path = os.path.join(PROJECT_ROOT, "logs", f"importlib_diagnosis_{get_utc_now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(all_diagnoses, f, indent=2, default=str)
    log(f"\nFull report saved to: {report_path}")
    
    return all_diagnoses

if __name__ == "__main__":
    main()