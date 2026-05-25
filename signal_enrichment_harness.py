#!/usr/bin/env python3
import sys
import os
import time
import json
import logging
import importlib
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, '/home/workspace/zo_sentinel')

WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://127.0.0.1:8772')
EXECUTE_URL = os.environ.get('EXECUTE_URL', 'http://127.0.0.1:8772/execute')
QUERY_URL = os.environ.get('QUERY_URL', 'http://127.0.0.1:8772/query')
LOG_FILE = '/tmp/signal_enrichment_harness.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('signal_enrichment_harness')


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_URL


def get_execute_url():
    return EXECUTE_URL


def get_db_path():
    return os.environ.get('DUCKDB_PATH', '/tmp/zo_sentinel.duckdb')


def ws_query(sql: str) -> list[dict]:
    try:
        import requests
        resp = requests.post(get_query_url(), json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list[dict]) -> bool:
    try:
        import requests
        resp = requests.post(get_write_url(), json={
            'table': table,
            'rows': rows,
            'wait': True
        }, timeout=60)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        import requests
        resp = requests.post(get_execute_url(), json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_enrichment_modules() -> list[tuple[str, Any]]:
    sentinels_dir = Path('/home/workspace/zo_sentinel')
    modules = []
    for path in sentinels_dir.glob('*_enrichment*.py'):
        if path.name.startswith('_'):
            continue
        module_name = path.stem
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                modules.append((module_name, module))
                log.info(f"Loaded enrichment module: {module_name}")
        except Exception as e:
            log.warning(f"Failed to load {module_name}: {e}")
    return modules


def verify_compute_score_signature(module: Any, module_name: str) -> bool:
    if not hasattr(module, 'compute_score'):
        log.warning(f"{module_name}: missing compute_score function")
        return False
    import inspect
    sig = inspect.signature(module.compute_score)
    params = list(sig.parameters.keys())
    if len(params) < 1:
        log.warning(f"{module_name}: compute_score requires at least 1 parameter (metadata)")
        return False
    return True


SYNTHETIC_METADATA_BATCH = [
    {
        'server_id': 'synth_test_001',
        'name': 'test-server-alpha',
        'url': 'https://registry.example.com/servers/alpha',
        'description': 'A well-maintained MCP server with strong community adoption and verified publisher',
        'trust_score': 0.85,
        'verdict': 'approved',
        'scan_count': 150,
        'registry_source': 'npm',
        'download_count': 50000,
        'dependency_count': 12,
        'publisher_verified': True,
        'stars': 1200,
        'first_seen': '2023-01-15T00:00:00Z',
        'last_updated': '2024-06-01T00:00:00Z',
        'tools': [{'name': 'read_file', 'description': 'Read files from disk', 'inputSchema': {}}, {'name': 'write_file', 'description': 'Write files to disk', 'inputSchema': {}}],
        'permission_scopes': ['filesystem:read', 'filesystem:write'],
        'auth_method': 'oauth2'
    },
    {
        'server_id': 'synth_test_002',
        'name': 'test-server-beta',
        'url': 'https://registry.example.com/servers/beta',
        'description': 'New MCP server with minimal documentation',
        'trust_score': 0.30,
        'verdict': 'pending',
        'scan_count': 5,
        'registry_source': 'github',
        'download_count': 100,
        'dependency_count': 3,
        'publisher_verified': False,
        'stars': 5,
        'first_seen': '2024-05-01T00:00:00Z',
        'last_updated': '2024-06-15T00:00:00Z',
        'tools': [{'name': 'query', 'description': 'Execute database query', 'inputSchema': {}}],
        'permission_scopes': ['database:admin'],
        'auth_method': 'api_key'
    },
    {
        'server_id': 'synth_test_003',
        'name': 'test-server-gamma',
        'url': 'https://registry.example.com/servers/gamma',
        'description': 'Experimental server with high permission requirements',
        'trust_score': 0.55,
        'verdict': 'approved',
        'scan_count': 45,
        'registry_source': 'smithery',
        'download_count': 5000,
        'dependency_count': 25,
        'publisher_verified': True,
        'stars': 350,
        'first_seen': '2023-06-20T00:00:00Z',
        'last_updated': '2024-05-20T00:00:00Z',
        'tools': [{'name': 'run_shell', 'description': 'Execute shell commands', 'inputSchema': {}}, {'name': 'install_pkg', 'description': 'Install system packages', 'inputSchema': {}}],
        'permission_scopes': ['shell:execute', 'sudo', 'network:full'],
        'auth_method': 'none'
    }
]


def validate_enrichment_module(module: Any, module_name: str) -> tuple[bool, str]:
    if not verify_compute_score_signature(module, module_name):
        return False, "Missing or invalid compute_score signature"
    
    scores = []
    for i, metadata in enumerate(SYNTHETIC_METADATA_BATCH):
        try:
            result = module.compute_score(metadata)
            if not isinstance(result, tuple) or len(result) != 2:
                return False, f"Test {i}: compute_score must return tuple[float, dict], got {type(result)}"
            score, evidence = result
            if not isinstance(score, (int, float)):
                return False, f"Test {i}: score must be numeric, got {type(score)}"
            if score < 0.0 or score > 1.0:
                return False, f"Test {i}: score {score} out of range [0.0, 1.0]"
            if not isinstance(evidence, dict):
                return False, f"Test {i}: evidence must be dict, got {type(evidence)}"
            scores.append(score)
        except Exception as e:
            return False, f"Test {i}: crashed with {e}"
    
    if len(set(scores)) == 1:
        return False, f"All 3 test scores identical ({scores[0]}) - would produce flat signal"
    
    return True, "OK"


def ensure_enrichments_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR,
        enrichment_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        computed_at VARCHAR,
        PRIMARY KEY (server_id, enrichment_name)
    )
    """
    return ws_execute(sql)


def ensure_mesh_memory_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mesh_memory (
        key VARCHAR,
        value VARCHAR,
        updated_at VARCHAR,
        PRIMARY KEY (key)
    )
    """
    return ws_execute(sql)


def is_enrichment_flat(enrichment_name: str) -> bool:
    rows = ws_query(f"SELECT value FROM mesh_memory WHERE key = 'enrichment_flat:{enrichment_name}'")
    if rows:
        try:
            val = json.loads(rows[0]['value'])
            if val.get('flat') is True:
                return True
        except:
            pass
    return False


def mark_enrichment_flat(enrichment_name: str) -> None:
    record = {
        'key': f'enrichment_flat:{enrichment_name}',
        'value': json.dumps({'flat': True, 'marked_at': get_utc_now_iso()}),
        'updated_at': get_utc_now_iso()
    }
    ws_write('mesh_memory', [record])
    log.warning(f"Enrichment '{enrichment_name}' marked as FLAT - skipping on next pass")


def get_pending_servers(enrichment_name: str, batch_size: int = 500) -> list[dict]:
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.description, r.trust_score, r.verdict,
           r.registry_source, r.scan_count,
           r.download_count, r.dependency_count, r.publisher_verified,
           r.stars, r.first_seen, r.last_updated, r.tools, r.permission_scopes,
           r.auth_method
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e 
        WHERE e.server_id = r.server_id AND e.enrichment_name = '{enrichment_name}'
    )
    LIMIT {batch_size}
    """
    return ws_query(sql)


def count_all_distinct_scores(enrichment_name: str) -> int:
    sql = f"""
    SELECT COUNT(DISTINCT score) as distinct_count
    FROM mcp_signal_enrichments
    WHERE enrichment_name = '{enrichment_name}'
    """
    rows = ws_query(sql)
    if rows:
        return rows[0].get('distinct_count', 0)
    return 0


def build_metadata_from_row(row: dict) -> dict:
    metadata = dict(row)
    if metadata.get('tools') and isinstance(metadata['tools'], str):
        try:
            metadata['tools'] = json.loads(metadata['tools'])
        except:
            metadata['tools'] = []
    if metadata.get('permission_scopes') and isinstance(metadata['permission_scopes'], str):
        try:
            metadata['permission_scopes'] = json.loads(metadata['permission_scopes'])
        except:
            metadata['permission_scopes'] = []
    if metadata.get('publisher_verified') in [0, '0', 'false', 'False']:
        metadata['publisher_verified'] = False
    if metadata.get('stars') is None:
        metadata['stars'] = 0
    if metadata.get('download_count') is None:
        metadata['download_count'] = 0
    if metadata.get('dependency_count') is None:
        metadata['dependency_count'] = 0
    return metadata


def run_single_enrichment(module: Any, module_name: str) -> tuple[int, int, bool]:
    if is_enrichment_flat(module_name):
        log.info(f"Enrichment '{module_name}' is marked flat - skipping")
        return 0, 0, False
    
    log.info(f"Processing enrichment '{module_name}'...")
    
    total_processed = 0
    total_written = 0
    batch_num = 0
    flat_detected = False
    run_scores = []
    
    while True:
        servers = get_pending_servers(module_name, batch_size=500)
        if not servers:
            log.info(f"No more pending servers for '{module_name}'")
            break
        
        batch_num += 1
        log.info(f"Batch {batch_num}: {len(servers)} servers to process")
        
        batch_records = []
        batch_scores = []
        
        for server in servers:
            server_id = server.get('server_id')
            if not server_id:
                continue
            
            try:
                metadata = build_metadata_from_row(server)
                score, evidence = module.compute_score(metadata)
                
                if not isinstance(score, (int, float)) or score < 0.0 or score > 1.0:
                    log.warning(f"{server_id}: invalid score {score} - skipping")
                    continue
                
                record = {
                    'server_id': server_id,
                    'enrichment_name': module_name,
                    'score': float(score),
                    'evidence': json.dumps(evidence),
                    'computed_at': get_utc_now_iso()
                }
                batch_records.append(record)
                batch_scores.append(float(score))
                total_processed += 1
                run_scores.append(float(score))
                
            except Exception as e:
                log.warning(f"{server_id}: compute_score failed: {e}")
                continue
        
        if batch_records:
            if ws_write('mcp_signal_enrichments', batch_records):
                total_written += len(batch_records)
                log.info(f"Wrote {len(batch_records)} enrichment records for batch {batch_num}")
            else:
                log.error(f"Failed to write batch {batch_num}")
        
        if batch_num % 10 == 0:
            log.info(f"Checkpoint after batch {batch_num}: {total_processed} processed, {total_written} written")
        
        if len(batch_records) < 500:
            break
    
    if total_processed >= 200:
        distinct = count_all_distinct_scores(module_name)
        log.info(f"Flat check for '{module_name}': {distinct} distinct scores across {total_processed} total")
        if distinct <= 1:
            mark_enrichment_flat(module_name)
            flat_detected = True
    
    return total_processed, total_written, flat_detected


def main():
    log.info("=" * 60)
    log.info("SIGNAL ENRICHMENT HARNESS - STARTING")
    log.info("=" * 60)
    
    start_time = time.time()
    
    if not ensure_enrichments_table():
        log.error("Failed to ensure mcp_signal_enrichments table")
        sys.exit(1)
    
    if not ensure_mesh_memory_table():
        log.error("Failed to ensure mesh_memory table")
        sys.exit(1)
    
    modules = discover_enrichment_modules()
    log.info(f"Discovered {len(modules)} enrichment modules")
    
    if not modules:
        log.error("No enrichment modules found")
        sys.exit(1)
    
    valid_modules = []
    for module_name, module in modules:
        ok, reason = validate_enrichment_module(module, module_name)
        if ok:
            valid_modules.append((module_name, module))
            log.info(f"VALIDATED: {module_name}")
        else:
            log.warning(f"REJECTED: {module_name} - {reason}")
    
    log.info(f"Validation complete: {len(valid_modules)}/{len(modules)} modules valid")
    
    if not valid_modules:
        log.error("No valid enrichment modules")
        sys.exit(1)
    
    grand_total_processed = 0
    grand_total_written = 0
    flat_count = 0
    
    for module_name, module in valid_modules:
        processed, written, flat = run_single_enrichment(module, module_name)
        grand_total_processed += processed
        grand_total_written += written
        if flat:
            flat_count += 1
        log.info(f"Completed '{module_name}': processed={processed}, written={written}, flat={flat}")
    
    elapsed = time.time() - start_time
    
    log.info("=" * 60)
    log.info("SIGNAL ENRICHMENT HARNESS - COMPLETE")
    log.info(f"  Duration: {elapsed:.1f}s")
    log.info(f"  Modules processed: {len(valid_modules)}")
    log.info(f"  Flat enrichments detected: {flat_count}")
    log.info(f"  Total records processed: {grand_total_processed}")
    log.info(f"  Total records written: {grand_total_written}")
    log.info("=" * 60)
    
    sys.exit(0)


if __name__ == '__main__':
    main()