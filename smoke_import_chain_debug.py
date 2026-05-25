import os
import sys
import traceback
import subprocess
import ast
import importlib

SERVICE_NAME = 'smoke_import_chain_diagnosis'
PROJECT_DIR = '/home/workspace/zo_sentinel'
OUTPUT_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

TARGET_MODULES = ['registry_api', 'rug_pull_monitor', 'signal_analyser']

def log(msg):
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'a') as f:
        f.write(f"{msg}\n")
    print(msg)

def check_sys_path():
    log("=== SYS.PATH CHECK ===")
    for p in sys.path:
        log(f"  {p}")
    project_in_path = PROJECT_DIR in sys.path or any(PROJECT_DIR in p for p in sys.path)
    log(f"Project dir in sys.path: {project_in_path}")

def capture_import_traceback(module_name):
    log(f"\n=== IMPORT TRACEBACK FOR: {module_name} ===")
    module_path = os.path.join(PROJECT_DIR, f"{module_name}.py")
    
    if not os.path.exists(module_path):
        log(f"  Module file not found: {module_path}")
        return None
    
    try:
        result = subprocess.run(
            [sys.executable, '-c', f'import sys; sys.path.insert(0, "{PROJECT_DIR}"); import {module_name}'],
            capture_output=True,
            text=True,
            timeout=30
        )
        log(f"  Return code: {result.returncode}")
        log(f"  STDOUT:\n{result.stdout}")
        log(f"  STDERR:\n{result.stderr}")
        
        if result.returncode != 0 and result.stderr:
            lines = result.stderr.strip().split('\n')
            for line in lines:
                log(f"    {line}")
        
        return result.stderr
    except Exception as e:
        log(f"  Exception during import: {e}")
        log(traceback.format_exc())
        return None

def extract_line_10_imports(module_path):
    log(f"\n=== LINE 10 IMPORTS FOR: {module_path} ===")
    try:
        with open(module_path, 'r') as f:
            lines = f.readlines()
        
        log(f"  Total lines: {len(lines)}")
        
        for i in range(min(15, len(lines))):
            line_num = i + 1
            line = lines[i].rstrip()
            log(f"    Line {line_num}: {line}")
        
        return lines
    except Exception as e:
        log(f"  Error reading file: {e}")
        return None

def check_circular_imports(module_name):
    log(f"\n=== CIRCULAR IMPORT CHECK: {module_name} ===")
    module_path = os.path.join(PROJECT_DIR, f"{module_name}.py")
    
    if not os.path.exists(module_path):
        return
    
    try:
        with open(module_path, 'r') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split('.')[0])
        
        log(f"  Direct imports: {imports}")
        
        for imp in imports:
            if imp in ['registry_api', 'rug_pull_monitor', 'signal_analyser']:
                log(f"  CIRCULAR CANDIDATE: imports {imp}")
                
    except Exception as e:
        log(f"  Error analyzing AST: {e}")

def check_write_service_imports(module_path):
    log(f"\n=== WRITE_SERVICE IMPORT CHECK: {module_path} ===")
    try:
        with open(module_path, 'r') as f:
            content = f.read()
        
        if 'write_service' in content.lower() or 'inference_router' in content.lower():
            log("  Module references write_service or inference_router")
            
            if 'import' in content:
                for line_num, line in enumerate(content.split('\n')[:30], 1):
                    if 'import' in line.lower() and ('write' in line.lower() or 'inference' in line.lower()):
                        log(f"    Line {line_num}: {line.strip()}")
        else:
            log("  No write_service or inference_router references found")
            
    except Exception as e:
        log(f"  Error checking write_service imports: {e}")

def check_module_dependencies(module_name):
    log(f"\n=== DEPENDENCY CHECK: {module_name} ===")
    module_path = os.path.join(PROJECT_DIR, f"{module_name}.py")
    
    if not os.path.exists(module_path):
        return
    
    try:
        with open(module_path, 'r') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        local_imports = []
        external_imports = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split('.')[0]
                    if name.startswith('_'):
                        continue
                    if name in ['sys', 'os', 'time', 'datetime', 'json', 'logging', 'requests', 'fastapi', 'uvicorn']:
                        continue
                    external_imports.append(name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod = node.module.split('.')[0]
                    if not mod.startswith('_') and mod not in ['sys', 'os', 'time', 'datetime', 'json', 'logging', 'requests']:
                        external_imports.append(f"{mod} (from)")
        
        unique_ext = sorted(set(external_imports))
        log(f"  External dependencies: {unique_ext}")
        
        for dep in unique_ext:
            dep_path = os.path.join(PROJECT_DIR, f"{dep}.py")
            if not os.path.exists(dep_path):
                log(f"    NOT FOUND locally: {dep}")
            else:
                log(f"    Found locally: {dep}")
                
    except Exception as e:
        log(f"  Error checking dependencies: {e}")

def run_diagnostic():
    log("=" * 60)
    log("SMOKE IMPORT CHAIN DIAGNOSIS")
    log("=" * 60)
    
    check_sys_path()
    
    for module in TARGET_MODULES:
        module_path = os.path.join(PROJECT_DIR, f"{module}.py")
        extract_line_10_imports(module_path)
        capture_import_traceback(module)
        check_circular_imports(module)
        check_write_service_imports(module_path)
        check_module_dependencies(module)
    
    log("\n" + "=" * 60)
    log("DIAGNOSIS COMPLETE")
    log("=" * 60)

if __name__ == '__main__':
    run_diagnostic()