#!/usr/bin/env python3
"""
ZO-SENTINEL Diagnostic Module: diagnose_importlib_failures_all.py
Inspects root cause of simultaneous importlib failures in:
  - registry_api.py
  - rug_pull_monitor.py
  - signal_analyser.py
"""

import sys
import os
import subprocess
import json
import traceback
from pathlib import Path
from datetime import datetime

OUTPUT_FILE = "/tmp/importlib_diagnosis_report.txt"
PIP_CMD = [sys.executable, "-m", "pip"]

def write(msg):
    print(msg)
    with open(OUTPUT_FILE, "a") as f:
        f.write(msg + "\n")

def run_cmd(cmd, capture=True):
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        else:
            result = subprocess.run(cmd, timeout=30)
            return "", "", result.returncode
    except Exception as e:
        return "", str(e), -1

def inspect_sys_path():
    write("\n" + "=" * 70)
    write("SECTION 1: sys.path and Module Search Path")
    write("=" * 70)
    write(f"Python executable: {sys.executable}")
    write(f"Python version: {sys.version}")
    write(f"\nCurrent sys.path entries ({len(sys.path)}):")
    for i, p in enumerate(sys.path):
        exists = "✓" if os.path.exists(p) else "✗ MISSING"
        write(f"  [{i}] {p} {exists}")

def inspect_importlib_bootstrap():
    write("\n" + "=" * 70)
    write("SECTION 2: importlib._bootstrap_external metadata")
    write("=" * 70)
    try:
        import importlib._bootstrap_external as bse
        write(f"Module file: {bse.__file__}")
        write(f"Module path: {getattr(bse, '__path__', 'N/A')}")
        write(f"Module loader: {type(bse)}")
    except Exception as e:
        write(f"Failed to inspect _bootstrap_external: {e}")

def check_dependencies():
    write("\n" + "=" * 70)
    write("SECTION 3: Dependency Version Checks")
    write("=" * 70)
    
    key_packages = [
        "fastapi", "uvicorn", "httpx", "asyncio", "importlib", 
        "sqlalchemy", "duckdb", "playwright", "beautifulsoup4",
        "lxml", "requests", "aiohttp"
    ]
    
    for pkg in key_packages:
        stdout, stderr, code = run_cmd(PIP_CMD + ["show", pkg])
        if code == 0 and stdout:
            write(f"\n--- {pkg} ---")
            for line in stdout.split("\n"):
                if line.startswith("Name:") or line.startswith("Version:") or line.startswith("Location:"):
                    write(f"  {line}")
        else:
            write(f"\n--- {pkg}: NOT INSTALLED or not found ---")

def check_requirements_file():
    write("\n" + "=" * 70)
    write("SECTION 4: Requirements Files")
    write("=" * 70)
    workspace = Path("/home/workspace")
    req_files = list(workspace.rglob("requirements*.txt")) + list(workspace.rglob("pyproject.toml"))
    for rf in req_files[:10]:
        write(f"\n--- {rf} ---")
        try:
            content = rf.read_text()[:500]
            write(content)
        except Exception as e:
            write(f"Could not read: {e}")

def simulate_importlib_failure():
    write("\n" + "=" * 70)
    write("SECTION 5: Simulating importlib failure scenario")
    write("=" * 70)
    
    # Attempt to import the problematic modules
    problematic_modules = [
        ("importlib", None),
        ("importlib._bootstrap_external", None),
    ]
    
    for mod_name, _ in problematic_modules:
        write(f"\nAttempting: {mod_name}")
        try:
            __import__(mod_name)
            write(f"  ✓ {mod_name} imported successfully")
        except Exception as e:
            write(f"  ✗ {mod_name} FAILED: {type(e).__name__}: {e}")
            write(f"     Traceback: {traceback.format_exc()}")

def check_frozen_modules():
    write("\n" + "=" * 70)
    write("SECTION 6: Frozen Module Detection")
    write("=" * 70)
    write(f"sys.frozen: {getattr(sys, 'frozen', False)}")
    write(f"sys._MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")
    
    # Check for .pyz embedded archives
    if hasattr(sys, 'executable'):
        exe_dir = os.path.dirname(sys.executable)
        write(f"\nExecutable directory: {exe_dir}")
        if os.path.exists(exe_dir):
            for f in os.listdir(exe_dir):
                if f.endswith('.pyz') or 'pyz' in f.lower():
                    write(f"  Found archive: {f}")

def inspect_module_cache():
    write("\n" + "=" * 70)
    write("SECTION 7: sys.modules cache inspection (importlib related)")
    write("=" * 70)
    import_keys = [k for k in sys.modules.keys() if 'importlib' in k.lower()]
    write(f"Found {len(import_keys)} importlib-related cached modules:")
    for k in import_keys[:20]:
        mod = sys.modules.get(k)
        write(f"  {k}: {type(mod).__name__ if mod else 'None'}")
        if hasattr(mod, '__file__') and mod.__file__:
            write(f"    file: {mod.__file__}")

def check_file_permissions():
    write("\n" + "=" * 70)
    write("SECTION 8: File permissions on key paths")
    write("=" * 70)
    check_paths = [
        "/home/workspace",
        "/home/workspace/zo_sentinel",
    ]
    for p in check_paths:
        if os.path.exists(p):
            st = os.stat(p)
            write(f"  {p}: mode={oct(st.st_mode)}, uid={st.st_uid}, gid={st.st_gid}")
        else:
            write(f"  {p}: DOES NOT EXIST")

def diagnose_external():
    write("\n" + "=" * 70)
    write("SECTION 9: External diagnostic commands")
    write("=" * 70)
    
    cmds = [
        ["python3", "-c", "import sys; print(sys.version)"],
        ["python3", "-c", "import importlib; print(importlib.__file__)"],
        ["python3", "-c", "import sys; print(sys.path[:3])"],
    ]
    for cmd in cmds:
        stdout, stderr, code = run_cmd(cmd)
        write(f"\nCMD: {' '.join(cmd)}")
        write(f"  stdout: {stdout[:200]}")
        if stderr:
            write(f"  stderr: {stderr[:200]}")

def main():
    # Initialize output file
    with open(OUTPUT_FILE, "w") as f:
        f.write(f"Importlib Failure Diagnosis Report\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Hostname: {os.environ.get('HOSTNAME', 'unknown')}\n")
    
    write(f"Diagnosis started at {datetime.now().isoformat()}")
    write(f"Output file: {OUTPUT_FILE}")
    
    inspect_sys_path()
    inspect_importlib_bootstrap()
    check_dependencies()
    check_requirements_file()
    simulate_importlib_failure()
    check_frozen_modules()
    inspect_module_cache()
    check_file_permissions()
    diagnose_external()
    
    write("\n" + "=" * 70)
    write("DIAGNOSIS COMPLETE")
    write("=" * 70)
    
    print(f"\nFull report written to: {OUTPUT_FILE}")
    
    # Also print summary
    print("\n--- QUICK SUMMARY ---")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Executable: {sys.executable}")
    print(f"sys.path entries: {len(sys.path)}")
    print(f"sys.frozen: {getattr(sys, 'frozen', False)}")
    
    # Check key imports
    critical = ["fastapi", "uvicorn", "httpx"]
    for pkg in critical:
        try:
            __import__(pkg)
            print(f"  ✓ {pkg}")
        except ImportError:
            print(f"  ✗ {pkg} MISSING")

if __name__ == "__main__":
    main()