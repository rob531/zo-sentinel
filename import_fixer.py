#!/usr/bin/env python3
"""
import_fixer.py - Fix importlib traceback failures in registry_api.py, rug_pull_monitor.py, signal_analyser.py
"""

import os
import sys
import re
import traceback

SERVICE_DIR = "/home/workspace/services"

TARGET_FILES = [
    "registry_api.py",
    "rug_pull_monitor.py", 
    "signal_analyser.py"
]

def check_file_imports(filepath):
    """Check file for import issues"""
    issues = []
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines[:15], 1):  # Check first 15 lines (imports area)
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if 'import' in stripped or 'from' in stripped:
            # Check for common import errors
            if '..' in stripped and '..' not in ('..'):
                issues.append(f"Line {i}: Suspicious relative import: {stripped}")
            if stripped.count('(') != stripped.count(')'):
                issues.append(f"Line {i}: Unbalanced parentheses: {stripped}")
    
    return issues

def fix_registry_api(filepath):
    """Fix registry_api.py import issues"""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Fix common import patterns for registry_api
    fixes_applied = []
    
    # Ensure proper imports exist
    import_block = """from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import requests
import logging
import sys
"""
    
    # Check if imports are broken - fix malformed ones
    lines = content.split('\n')
    new_lines = []
    import_started = False
    import_done = False
    
    for line in lines:
        stripped = line.strip()
        
        # Skip broken import lines that cause issues
        if 'import uvicorn' in line or 'from uvicorn' in line:
            if 'uvicorn' not in '\n'.join(new_lines):
                new_lines.append('import uvicorn')
            continue
        if 'import logging' in line or 'from logging' in line:
            if 'logging' not in '\n'.join(new_lines):
                new_lines.append('import logging')
            continue
        if 'import requests' in line or 'from requests' in line:
            if 'requests' not in '\n'.join(new_lines):
                new_lines.append('import requests')
            continue
            
        new_lines.append(line)
    
    new_content = '\n'.join(new_lines)
    
    # Add missing imports at the top
    header_imports = "import logging\nimport sys\n"
    
    if 'import logging' not in new_content:
        # Insert after existing imports
        lines = new_content.split('\n')
        for i, line in enumerate(lines):
            if (line.strip().startswith('import') or line.strip().startswith('from')):
                if i > 0 and i < 20:
                    pass
    
    return new_content, fixes_applied

def fix_rug_pull_monitor(filepath):
    """Fix rug_pull_monitor.py import issues"""
    with open(filepath, 'r') as f:
        content = f.read()
    
    fixes_applied = []
    
    # Ensure asyncio is imported
    if 'import asyncio' not in content:
        content = "import asyncio\nimport sys\n" + content
    
    # Ensure requests is imported
    if 'import requests' not in content and 'from requests' not in content:
        content = content.replace('import asyncio\nimport sys\n', 'import asyncio\nimport sys\nimport requests\n')
    
    return content, fixes_applied

def fix_signal_analyser(filepath):
    """Fix signal_analyser.py import issues"""
    with open(filepath, 'r') as f:
        content = f.read()
    
    fixes_applied = []
    
    # Ensure proper async imports
    if 'import asyncio' not in content:
        content = "import asyncio\n" + content
        fixes_applied.append("Added missing asyncio import")
    
    return content, fixes_applied

def main():
    print("=== Import Fixer for ZO-SENTINEL ===\n")
    
    for filename in TARGET_FILES:
        filepath = os.path.join(SERVICE_DIR, filename)
        
        if not os.path.exists(filepath):
            # Try alternate path
            alt_path = os.path.join("/home/workspace", filename)
            if os.path.exists(alt_path):
                filepath = alt_path
        
        print(f"Checking: {filepath}")
        
        if not os.path.exists(filepath):
            print(f"  [SKIP] File not found\n")
            continue
        
        try:
            # Check current issues
            issues = check_file_imports(filepath)
            if issues:
                print(f"  Found issues: {issues}")
            
            # Try to import the module to see actual error
            module_name = filename[:-3]  # Remove .py
            try:
                import importlib
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    print(f"  [OK] Module imports successfully")
            except Exception as e:
                print(f"  [ERROR] Import failed: {e}")
                
                # Apply fixes based on filename
                if 'registry_api' in filename:
                    new_content, fixes = fix_registry_api(filepath)
                elif 'rug_pull' in filename:
                    new_content, fixes = fix_rug_pull_monitor(filepath)
                elif 'signal' in filename:
                    new_content, fixes = fix_signal_analyser(filepath)
                else:
                    new_content = None
                    fixes = []
                
                if new_content:
                    # Backup original
                    backup_path = filepath + '.bak'
                    with open(backup_path, 'w') as f:
                        with open(filepath, 'r') as orig:
                            f.write(orig.read())
                    print(f"  [BACKUP] Saved to {backup_path}")
                    
                    # Write fixed version
                    with open(filepath, 'w') as f:
                        f.write(new_content)
                    print(f"  [FIXED] Applied {len(fixes)} fixes: {fixes}")
                    
        except Exception as e:
            print(f"  [ERROR] {e}")
            traceback.print_exc()
        
        print()

if __name__ == '__main__':
    main()