import os
import sys
import ast
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/signal_weak_signal_diagnosis.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'signal_weak_signal_diagnosis'
WRITE_SERVICE_URL = 'http://localhost:8772'
SENTINEL_PATH = Path('/home/workspace/zo_sentinel')

def ws_query(sql, params=None):
    payload = {'table': '_internal', 'sql': sql, 'params': params or [], 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def extract_imports_from_source(file_path):
    imports = []
    try:
        with open(file_path, 'r') as f:
            source = f.read()
        tree = ast.parse(source, filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(('import', alias.name, alias.asname or alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append(('from', f'{module}.{alias.name}' if module else alias.name, alias.asname or alias.name))
    except Exception as e:
        logger.error(f"Failed to parse {file_path}: {e}")
    return imports

def check_service_health_records():
    logger.info("Querying service_health for recent failures...")
    sql = """
    SELECT service_name, status, last_heartbeat, meta
    FROM service_health
    WHERE last_heartbeat >= NOW() - INTERVAL '2 hours'
    ORDER BY last_heartbeat DESC
    LIMIT 50
    """
    try:
        records = ws_query(sql)
        failed = [r for r in records if r.get('status') == 'error' or 'failure' in str(r.get('meta', '')).lower()]
        logger.info(f"Found {len(failed)} recent failures/errors in service_health")
        for r in failed[:10]:
            logger.info(f"  {r.get('service_name')}: {r.get('status')} @ {r.get('last_heartbeat')} | {r.get('meta', '')[:200]}")
        return records
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
        return []

def check_sys_path():
    logger.info("Checking sys.path entries...")
    for i, p in enumerate(sys.path):
        logger.info(f"  [{i}] {p}")
    return sys.path

def check_target_files(target_names):
    results = {}
    for name in target_names:
        file_path = SENTINEL_PATH / name
        logger.info(f"\nAnalyzing {name}...")
        if not file_path.exists():
            logger.warning(f"  File not found: {file_path}")
            results[name] = {'exists': False}
            continue
        
        logger.info(f"  File exists: {file_path}")
        file_size = file_path.stat().st_size
        logger.info(f"  Size: {file_size} bytes")
        
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        logger.info(f"  Total lines: {len(lines)}")
        
        if len(lines) >= 10:
            logger.info(f"  Line 10: {repr(lines[9].rstrip())}")
        
        imports = extract_imports_from_source(file_path)
        results[name] = {
            'exists': True,
            'size': file_size,
            'imports': imports,
            'line_10': lines[9].rstrip() if len(lines) >= 10 else None
        }
        
        logger.info(f"  Found {len(imports)} import statements:")
        for imp_type, imp_name, imp_as in imports[:20]:
            logger.info(f"    {imp_type}: {imp_name} (as {imp_as})")
    
    return results

def trace_import_chain(import_name, search_paths=None):
    if search_paths is None:
        search_paths = sys.path + [str(SENTINEL_PATH)]
    
    logger.info(f"\nTracing import: {import_name}")
    for sp in search_paths:
        if not sp:
            continue
        for ext in ['', '.py', '/__init__.py']:
            candidate = Path(sp) / f"{import_name.replace('.', '/')}{ext}"
            if candidate.exists():
                logger.info(f"  FOUND: {candidate}")
                return str(candidate)
            candidate_dir = Path(sp) / import_name.replace('.', '/')
            if candidate_dir.is_dir():
                init_file = candidate_dir / '__init__.py'
                if init_file.exists():
                    logger.info(f"  FOUND: {init_file}")
                    return str(init_file)
    logger.warning(f"  NOT FOUND in any path")
    return None

def diagnose_line10_import_errors(target_files):
    logger.info("\n=== DIAGNOSING LINE 10 IMPORT ERRORS ===")
    
    all_imports = {}
    for name, info in target_files.items():
        if not info.get('exists'):
            continue
        for imp_type, imp_name, imp_as in info.get('imports', []):
            if imp_name not in all_imports:
                all_imports[imp_name] = []
            all_imports[imp_name].append(name)
    
    logger.info(f"\nImport names shared across files:")
    shared = {k: v for k, v in all_imports.items() if len(v) > 1}
    for imp_name, files in shared.items():
        logger.info(f"  {imp_name}: used in {files}")
    
    logger.info(f"\nChecking if any shared imports cannot be resolved...")
    unresolved = []
    for imp_name in shared.keys():
        resolved = trace_import_chain(imp_name)
        if resolved is None:
            unresolved.append(imp_name)
            logger.error(f"  UNRESOLVED: {imp_name}")
    
    if unresolved:
        logger.error(f"\n*** CRITICAL: {len(unresolved)} unresolved imports could cause identical line 10 failures:")
        for u in unresolved:
            logger.error(f"    - {u}")
            trace_import_chain(u)
    else:
        logger.info("\nAll shared imports resolve successfully.")
    
    logger.info("\nChecking for line 10 patterns...")
    line10_contents = {}
    for name, info in target_files.items():
        lc10 = info.get('line_10')
        if lc10:
            line10_contents[name] = lc10
    
    if line10_contents:
        unique_line10 = set(line10_contents.values())
        if len(unique_line10) == 1:
            single = list(unique_line10)[0]
            logger.warning(f"  ALL files have IDENTICAL line 10: {repr(single)}")
        else:
            logger.info(f"  Line 10 differs across files:")
            for name, content in line10_contents.items():
                logger.info(f"    {name}: {repr(content)}")
    
    return unresolved

def main():
    logger.info("=" * 60)
    logger.info(f"Signal Weak Signal Import Chain Diagnosis")
    logger.info(f"Started: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    target_files = ['signal_analyser.py', 'rug_pull_monitor.py', 'import_failure_root_cause_v2.py']
    
    logger.info("\n[1] Checking sys.path entries...")
    path_entries = check_sys_path()
    
    logger.info("\n[2] Querying service_health for recent failures...")
    health_records = check_service_health_records()
    
    logger.info("\n[3] Extracting imports from target files...")
    file_info = check_target_files(target_files)
    
    logger.info("\n[4] Tracing import chain for shared imports...")
    unresolved = diagnose_line10_import_errors(file_info)
    
    logger.info("\n[5] Computing file hashes for comparison...")
    for name in target_files:
        fp = SENTINEL_PATH / name
        if fp.exists():
            with open(fp, 'rb') as f:
                content = f.read()
            md5 = hashlib.md5(content).hexdigest()
            logger.info(f"  {name}: MD5={md5}")
    
    diagnosis_summary = {
        'diagnosis_ts': datetime.now(timezone.utc).isoformat(),
        'sys_path_count': len(path_entries),
        'health_records_checked': len(health_records),
        'target_files_analyzed': list(file_info.keys()),
        'unresolved_imports': unresolved,
        'likely_cause': 'Shared unresolved import at line 10' if unresolved else 'Indeterminate - imports resolved'
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSIS SUMMARY:")
    for k, v in diagnosis_summary.items():
        logger.info(f"  {k}: {v}")
    logger.info("=" * 60)
    
    logger.info("\nDiagnosis complete.")
    sys.exit(0)

if __name__ == '__main__':
    main()