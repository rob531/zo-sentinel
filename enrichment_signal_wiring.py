#!/usr/bin/env python3
"""
enrichment_signal_wiring.py
Wires community_signal, tool_description_safety, permission_scope, and
temporal_stability enrichment modules into signal_analyser_v2 pipeline.

PURPOSE: Bridge enrichment module outputs to mcp_signal_enrichments table
         via write_service. Addresses discrimination gap where 3 signals
         show only 4 distinct values despite module existence.

AUTHOR: zo_sentinel_builder
CREATED: 2026-05-28
"""

import sys
import os
import time
import signal
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

sys.path.insert(0, '/home/workspace/zo_sentinel')

from db_utils import ws_query, ws_write, ws_execute

SERVICE_NAME = "enrichment_signal_wiring"
SERVICE_PORT = 8772
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
PID_FILE = "/tmp/enrichment_signal_wiring.pid"
LOG_FILE = "/home/workspace/logs/enrichment_signal_wiring.log"
POLL_SECS = 60
HEARTBEAT_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

_process_start_time = None
_enrichment_modules = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_deterministic_id(server_id: str, signal_type: str) -> str:
    content = f"{server_id}:{signal_type}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            log.error(f"Instance already running as PID {old_pid}")
            return False
        else:
            log.warning(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def load_enrichment_modules() -> Dict[str, Any]:
    modules = {}
    
    try:
        from community_signal_enrichment import compute_score as community_score
        modules['community_signal'] = {
            'func': community_score,
            'signal_type': 'community_signal',
            'max_score': 100.0
        }
        log.info("Loaded community_signal_enrichment")
    except ImportError as e:
        log.warning(f"Failed to load community_signal_enrichment: {e}")
    
    try:
        from tool_description_safety_enrichment import compute_score as tool_desc_score
        modules['tool_description_safety'] = {
            'func': tool_desc_score,
            'signal_type': 'tool_description_safety',
            'max_score': 100.0
        }
        log.info("Loaded tool_description_safety_enrichment")
    except ImportError as e:
        log.warning(f"Failed to load tool_description_safety_enrichment: {e}")
    
    try:
        from permission_scope_enrichment import compute_score as permission_score
        modules['permission_scope'] = {
            'func': permission_score,
            'signal_type': 'permission_scope',
            'max_score': 100.0
        }
        log.info("Loaded permission_scope_enrichment")
    except ImportError as e:
        log.warning(f"Failed to load permission_scope_enrichment: {e}")
    
    try:
        from temporal_stability_enrichment import compute_score as temporal_score
        modules['temporal_stability'] = {
            'func': temporal_score,
            'signal_type': 'temporal_stability',
            'max_score': 100.0
        }
        log.info("Loaded temporal_stability_enrichment")
    except ImportError as e:
        log.warning(f"Failed to load temporal_stability_enrichment: {e}")
    
    return modules


def get_unenriched_servers(limit: int = 100) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        registry_source,
        first_seen,
        last_seen,
        ecosystems_metadata,
        tool_names,
        permission_list
    FROM mcp_server_registry
    WHERE server_id IS NOT NULL
    ORDER BY last_seen DESC
    LIMIT ?
    """
    try:
        result = ws_query(sql, params=[limit])
        rows = result.get('rows', [])
        
        unenriched = []
        for row in rows:
            server_id = row.get('server_id')
            if not server_id:
                continue
            
            check_sql = """
            SELECT COUNT(*) as cnt 
            FROM mcp_signal_enrichments 
            WHERE server_id = ?
            """
            check_result = ws_query(check_sql, params=[server_id])
            count_rows = check_result.get('rows', [])
            if count_rows and count_rows[0].get('cnt', 0) == 0:
                unenriched.append(row)
        
        return unenriched
    except Exception as e:
        log.error(f"Failed to query unenriched servers: {e}")
        return []


def get_all_servers_for_enrichment(limit: int = 100) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        registry_source,
        first_seen,
        last_seen,
        ecosystems_metadata,
        tool_names,
        permission_list
    FROM mcp_server_registry
    WHERE server_id IS NOT NULL
    ORDER BY last_seen DESC
    LIMIT ?
    """
    try:
        result = ws_query(sql, params=[limit])
        return result.get('rows', [])
    except Exception as e:
        log.error(f"Failed to query servers for enrichment: {e}")
        return []


def build_metadata_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {
        'server_id': row.get('server_id'),
        'name': row.get('name', ''),
        'url': row.get('url', ''),
        'description': row.get('description', ''),
        'registry_source': row.get('registry_source', 'unknown'),
        'first_seen': row.get('first_seen'),
        'last_seen': row.get('last_seen'),
        'last_updated': row.get('last_seen'),
        'last_scanned': row.get('last_scanned'),
        'scan_count': row.get('scan_count', 0),
    }
    
    ecosystems = row.get('ecosystems_metadata')
    if ecosystems:
        if isinstance(ecosystems, str):
            import json
            try:
                ecosystems = json.loads(ecosystems)
            except:
                ecosystems = {}
        if isinstance(ecosystems, dict):
            metadata.update({
                'stars': ecosystems.get('stars', 0),
                'forks': ecosystems.get('forks', 0),
                'download_count': ecosystems.get('download_count', 0),
                'dependent_count': ecosystems.get('dependent_count', 0),
                'contributor_count': ecosystems.get('contributor_count', 0),
                'publisher_verified': ecosystems.get('publisher_verified', False),
                'age_days': ecosystems.get('age_days', 0),
                'update_frequency': ecosystems.get('update_frequency', 0),
                'release_cadence': ecosystems.get('release_cadence', 'unknown'),
            })
    
    tool_names = row.get('tool_names')
    if tool_names:
        if isinstance(tool_names, str):
            import json
            try:
                tool_names = json.loads(tool_names)
            except:
                tool_names = []
        if isinstance(tool_names, list):
            metadata['tools'] = []
            for t in tool_names:
                if isinstance(t, dict):
                    metadata['tools'].append(t)
                elif isinstance(t, str):
                    metadata['tools'].append({'name': t, 'description': ''})
    
    permission_list = row.get('permission_list')
    if permission_list:
        if isinstance(permission_list, str):
            import json
            try:
                permission_list = json.loads(permission_list)
            except:
                permission_list = []
        if isinstance(permission_list, list):
            metadata['permissions'] = permission_list
    
    return metadata


def compute_enrichment_score(
    module_info: Dict[str, Any],
    metadata: Dict[str, Any]
) -> Tuple[float, Dict[str, Any]]:
    func = module_info['func']
    signal_type = module_info['signal_type']
    
    try:
        if signal_type == 'community_signal':
            result = func(metadata)
        elif signal_type == 'tool_description_safety':
            result = func(metadata)
        elif signal_type == 'permission_scope':
            result = func(metadata)
        elif signal_type == 'temporal_stability':
            result = func(metadata)
        else:
            return (0.0, {'error': f'Unknown signal type: {signal_type}'})
        
        if isinstance(result, tuple) and len(result) == 2:
            score, evidence = result
            return (float(score), evidence)
        elif isinstance(result, dict):
            return (float(result.get('score', 0.0)), result.get('evidence', result))
        elif isinstance(result, (int, float)):
            return (float(result), {'raw_score': result})
        else:
            return (0.0, {'error': 'Unexpected return type'})
            
    except Exception as e:
        return (0.0, {'error': str(e), 'signal_type': signal_type})


def write_enrichment_row(
    server_id: str,
    signal_type: str,
    score: float,
    evidence: Dict[str, Any]
) -> bool:
    enrichment_id = compute_deterministic_id(server_id, signal_type)
    now = utc_now_iso()
    
    import json
    evidence_blob = json.dumps({
        'signal_type': signal_type,
        'confidence': min(1.0, score / 100.0),
        'evidence': evidence,
        'computed_at': now
    })
    
    row = {
        'enrichment_id': enrichment_id,
        'server_id': server_id,
        'signal_type': signal_type,
        'score': score,
        'evidence_blob': evidence_blob,
        'computed_at': now,
        'source_module': f'enrichment_signal_wiring'
    }
    
    try:
        ws_write('mcp_signal_enrichments', [row])
        return True
    except Exception as e:
        log.error(f"Failed to write enrichment for {server_id}/{signal_type}: {e}")
        return False


def ensure_enrichments_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        enrichment_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE,
        evidence_blob TEXT,
        computed_at TIMESTAMPTZ,
        source_module VARCHAR,
        UNIQUE(server_id, signal_type)
    )
    """
    try:
        ws_execute(sql)
        log.info("Ensured mcp_signal_enrichments table exists")
    except Exception as e:
        log.warning(f"Table creation note: {e}")


def send_heartbeat() -> None:
    global _process_start_time
    if _process_start_time is None:
        _process_start_time = time.time()
    
    uptime = int(time.time() - _process_start_time)
    
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': 'running',
        'uptime_seconds': uptime,
        'meta': {
            'modules_loaded': list(_enrichment_modules.keys()),
            'heartbeat_interval': HEARTBEAT_INTERVAL
        }
    }
    
    try:
        ws_write('service_health', [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def process_server_enrichments(
    server_id: str,
    metadata: Dict[str, Any]
) -> int:
    written = 0
    
    for signal_key, module_info in _enrichment_modules.items():
        score, evidence = compute_enrichment_score(module_info, metadata)
        
        if write_enrichment_row(server_id, module_info['signal_type'], score, evidence):
            written += 1
            log.debug(f"Wrote {module_info['signal_type']}={score:.2f} for {server_id}")
    
    return written


def cycle() -> int:
    global _enrichment_modules
    
    if not _enrichment_modules:
        log.info("Loading enrichment modules...")
        _enrichment_modules = load_enrichment_modules()
        log.info(f"Loaded {len(_enrichment_modules)} enrichment modules")
    
    ensure_enrichments_table()
    
    servers = get_all_servers_for_enrichment(limit=50)
    log.info(f"Processing {len(servers)} servers for enrichment")
    
    total_written = 0
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        metadata = build_metadata_dict(server)
        written = process_server_enrichments(server_id, metadata)
        total_written += written
    
    log.info(f"Cycle complete: {total_written} enrichment records written")
    return total_written


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    log.info(f"Write service URL: {WRITE_SERVICE_URL}")
    log.info(f"PID file: {PID_FILE}")
    
    if not check_single_instance():
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    _enrichment_modules = load_enrichment_modules()
    log.info(f"Loaded {len(_enrichment_modules)} enrichment modules")
    
    _process_start_time = time.time()
    send_heartbeat()
    
    log.info(f"Entering main loop (poll every {POLL_SECS}s)")
    
    while True:
        try:
            cycle()
            send_heartbeat()
        except Exception as e:
            log.error(f"Error in main cycle: {e}", exc_info=True)
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()
else:
    _enrichment_modules = load_enrichment_modules()