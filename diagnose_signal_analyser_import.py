#!/usr/bin/env python3
"""
Diagnostic module to identify root cause of import failures in signal_analyser.py.
Smoke failures show traceback ending at '<frozen importlib' for registry_api.py, 
rug_pull_monitor.py, and signal_analyser.py.
"""

import sys
import os
import traceback

# Add workspace to path
sys.path.insert(0, '/home/workspace')

def read_source_file(filepath):
    """Read and return source content of a file."""
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except Exception as e:
        return f"ERROR reading {filepath}: {e}"

def check_module_symbols(module_name, expected_symbols):
    """Check if a module exports expected symbols."""
    results = []
    try:
        mod = __import__(module_name, fromlist=expected_symbols)
        for sym in expected_symbols:
            if hasattr(mod, sym):
                results.append(f"  ✓ {sym} found in {module_name}")
            else:
                results.append(f"  ✗ {sym} NOT found in {module_name}")
    except Exception as e:
        results.append(f"  ✗ Cannot import {module_name}: {e}")
    return results

def check_duckdb_schema():
    """Query DuckDB schema to verify table/column existence."""
    import requests
    results = []
    
    tables = ['mcp_server_registry', 'mcp_signal_scores', 'mcp_threat_associations', 
              'mcp_risk_register', 'audit_log', 'auth_tokens', 'service_health']
    
    for table in tables:
        try:
            resp = requests.post('http://127.0.0.1:8772/query', json={
                "sql": f"SELECT * FROM {table} LIMIT 0"
            }, timeout=5)
            if resp.status_code == 200:
                results.append(f"  ✓ Table '{table}' exists")
                # Get column info
                resp_cols = requests.post('http://127.0.0.1:8772/query', json={
                    "sql": f"DESCRIBE {table}"
                }, timeout=5)
                if resp_cols.status_code == 200:
                    cols = resp_cols.json().get('rows', [])
                    col_names = [c['column_name'] for c in cols]
                    results.append(f"    Columns: {', '.join(col_names)}")
            else:
                results.append(f"  ✗ Table '{table}' - query failed: {resp.status_code}")
        except Exception as e:
            results.append(f"  ✗ Table '{table}' - error: {e}")
    
    return results

def run_diagnostics():
    print("=" * 70)
    print("SIGNAL ANALYSER IMPORT DIAGNOSTIC REPORT")
    print("=" * 70)
    
    # Step 1: Read signal_analyser.py source
    print("\n[1] SIGNAL_ANALYSER.PY SOURCE INSPECTION")
    print("-" * 50)
    
    signal_analyser_path = '/home/workspace/signal_analyser.py'
    if os.path.exists(signal_analyser_path):
        source = read_source_file(signal_analyser_path)
        print(f"File: {signal_analyser_path}")
        print(f"Lines: {source.count(chr(10)) + 1}")
        print("\n--- Import statements found ---")
        for line in source.split('\n'):
            if line.strip().startswith('import ') or line.strip().startswith('from '):
                print(f"  {line.strip()}")
    else:
        print(f"  ✗ File not found: {signal_analyser_path}")
    
    # Step 2: Check dependency modules
    print("\n[2] DEPENDENCY MODULE SYMBOL CHECK")
    print("-" * 50)
    
    # Common imports to check based on the traceback mentions
    modules_to_check = [
        ('registry_api', ['SignalRegistry']),
        ('rug_pull_monitor', ['RugPullMonitor']),
        ('rule_engine_api', ['RuleEngine']),
        ('inference_router', ['InferenceRouter']),
        ('advanced_filter_api', ['FilterEngine']),
    ]
    
    for module_name, expected_symbols in modules_to_check:
        print(f"\nChecking module: {module_name}")
        for result in check_module_symbols(module_name, expected_symbols):
            print(result)
    
    # Step 3: Check registry_api.py specifically (mentioned in traceback)
    print("\n[3] REGISTRY_API.PY INSPECTION")
    print("-" * 50)
    
    registry_api_path = '/home/workspace/registry_api.py'
    if os.path.exists(registry_api_path):
        source = read_source_file(registry_api_path)
        print(f"File: {registry_api_path}")
        print("\n--- Classes/functions exported ---")
        for line in source.split('\n'):
            stripped = line.strip()
            if stripped.startswith('class ') or stripped.startswith('def '):
                print(f"  {stripped}")
        print("\n--- All import statements ---")
        for line in source.split('\n'):
            if line.strip().startswith('import ') or line.strip().startsfrom('from '):
                print(f"  {line.strip()}")
    else:
        print(f"  ✗ File not found: {registry_api_path}")
    
    # Step 4: Check rug_pull_monitor.py specifically
    print("\n[4] RUG_PULL_MONITOR.PY INSPECTION")
    print("-" * 50)
    
    rug_pull_path = '/home/workspace/rug_pull_monitor.py'
    if os.path.exists(rug_pull_path):
        source = read_source_file(rug_pull_path)
        print(f"File: {rug_pull_path}")
        print("\n--- Classes/functions exported ---")
        for line in source.split('\n'):
            stripped = line.strip()
            if stripped.startswith('class ') or stripped.startswith('def '):
                print(f"  {stripped}")
    else:
        print(f"  ✗ File not found: {rug_pull_path}")
    
    # Step 5: DuckDB Schema Verification
    print("\n[5] DUCKB SCHEMA VERIFICATION")
    print("-" * 50)
    
    try:
        for result in check_duckdb_schema():
            print(result)
    except Exception as e:
        print(f"  ✗ DuckDB connection failed: {e}")
    
    # Step 6: Attempt direct import of signal_analyser
    print("\n[6] DIRECT IMPORT TEST OF SIGNAL_ANALYSER")
    print("-" * 50)
    
    print("Attempting: from signal_analyser import ...")
    try:
        # Clear any cached modules
        for mod in list(sys.modules.keys()):
            if 'signal_analyser' in mod or 'registry_api' in mod or 'rug_pull' in mod:
                del sys.modules[mod]
        
        import signal_analyser
        print("  ✓ signal_analyser imported successfully")
        
        # Check what it exports
        if hasattr(signal_analyser, '__all__'):
            print(f"  Exports (from __all__): {signal_analyser.__all__}")
        else:
            exports = [n for n in dir(signal_analyser) if not n.startswith('_')]
            print(f"  Exports: {exports}")
            
    except ImportError as e:
        print(f"  ✗ Import FAILED: {e}")
        print("\n  Full traceback:")
        traceback.print_exc()
    except Exception as e:
        print(f"  ✗ Unexpected error: {e}")
        traceback.print_exc()
    
    # Step 7: Analyze imports in signal_analyser
    print("\n[7] SIGNAL_ANALYSER IMPORT RESOLUTION")
    print("-" * 50)
    
    signal_analyser_path = '/home/workspace/signal_analyser.py'
    if os.path.exists(signal_analyser_path):
        source = read_source_file(signal_analyser_path)
        print("\nAttempting to resolve each import...")
        
        import re
        import_pattern = re.compile(r'^(?:from|import)\s+([\w\.]+)', re.MULTILINE)
        
        for match in import_pattern.finditer(source):
            module_path = match.group(1).split('.')[0]
            print(f"\n  Checking: {match.group(0).strip()}")
            try:
                mod = __import__(module_path, fromlist=[''])
                print(f"    ✓ {module_path} resolves to {mod.__file__}")
            except ImportError as ie:
                print(f"    ✗ {module_path} FAILED: {ie}")
            except Exception as ex:
                print(f"    ✗ {module_path} error: {ex}")
    
    print("\n" + "=" * 70)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 70)

if __name__ == '__main__':
    run_diagnostics()