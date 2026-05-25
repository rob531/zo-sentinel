import logging
import os
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/integrate_weak_signals_v2_enrichments.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'integrate_weak_signals_v2_enrichments'
SERVICE_PORT = 8799
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
POLL_SECS = 300
ENRICHMENT_BATCH_SIZE = 50

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
ENRICHMENT_DIR = PROJECT_DIR / 'enrichments'


def ws_query(sql):
    resp = requests.post(
        QUERY_SERVICE_URL,
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table, rows):
    resp = requests.post(
        WRITE_SERVICE_URL,
        json={'table': table, 'rows': rows, 'wait': True},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    resp = requests.post(
        EXECUTE_SERVICE_URL,
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f'{SERVICE_NAME} already running as PID {old_pid}')
            sys.exit(1)
        except (OSError, ProcessLookupError):
            logger.warning(f'Stale PID file {old_pid}, removing')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    logger.info(f'PID {os.getpid()} written to {PID_FILE}')


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    logger.info(f'Received {sig_name}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status='running', meta=None):
    ts = utc_now_iso()
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': status,
        'meta': meta or {}
    }
    try:
        ws_write('service_health', [row])
    except Exception as e:
        logger.warning(f'Heartbeat failed: {e}')


def load_enrichment_modules():
    modules = {}
    
    sys.path.insert(0, str(ENRICHMENT_DIR))
    
    try:
        import temporal_stability_enrichment_v2
        modules['temporal_stability'] = temporal_stability_enrichment_v2
        logger.info('Loaded temporal_stability_enrichment_v2')
    except ImportError as e:
        logger.error(f'Failed to import temporal_stability_enrichment_v2: {e}')
    
    try:
        import tool_description_safety_enrichment_v2
        modules['tool_description_safety'] = tool_description_safety_enrichment_v2
        logger.info('Loaded tool_description_safety_enrichment_v2')
    except ImportError as e:
        logger.error(f'Failed to import tool_description_safety_enrichment_v2: {e}')
    
    try:
        import permission_scope_enrichment_v2
        modules['permission_scope'] = permission_scope_enrichment_v2
        logger.info('Loaded permission_scope_enrichment_v2')
    except ImportError as e:
        logger.error(f'Failed to import permission_scope_enrichment_v2: {e}')
    
    return modules


def get_unenriched_servers(signal_type, limit=ENRICHMENT_BATCH_SIZE):
    sql = f"""
    SELECT r.server_id, r.name, r.description, r.url, r.registry_source,
           e.first_published_at, e.last_updated_at, e.download_count,
           e.dependent_package_count, e.stars, e.forks, e.open_issues,
           e.contributor_count, e.readme_length, e.license_type,
           e.has_security_policy, e.has_code_of_conduct, e.maintainer_count
    FROM mcp_server_registry r
    LEFT JOIN mcp_ecosystems_metadata e ON r.server_id = e.server_id
    LEFT JOIN (
        SELECT server_id FROM mcp_signal_enrichments 
        WHERE signal_type = '{signal_type}'
    ) enriched ON r.server_id = enriched.server_id
    WHERE enriched.server_id IS NULL
    AND r.verdict != 'KNOWN_THREAT'
    LIMIT {limit}
    """
    try:
        return ws_query(sql)
    except Exception as e:
        logger.error(f'Failed to query unenriched servers for {signal_type}: {e}')
        return []


def compute_temporal_stability_score(server_data, module):
    if not hasattr(module, 'compute_score'):
        logger.warning('temporal_stability_enrichment_v2 has no compute_score')
        return None
    
    metadata = {
        'first_published_at': server_data.get('first_published_at'),
        'last_updated_at': server_data.get('last_updated_at'),
        'registry_source': server_data.get('registry_source'),
        'url': server_data.get('url'),
    }
    
    try:
        result = module.compute_score(metadata)
        return result
    except Exception as e:
        logger.debug(f'compute_score failed for {server_data.get("server_id")}: {e}')
        return None


def compute_tool_description_safety_score(server_data, module):
    if not hasattr(module, 'compute_score'):
        logger.warning('tool_description_safety_enrichment_v2 has no compute_score')
        return None
    
    metadata = {
        'name': server_data.get('name'),
        'description': server_data.get('description'),
        'registry_source': server_data.get('registry_source'),
    }
    
    try:
        result = module.compute_score(metadata)
        return result
    except Exception as e:
        logger.debug(f'compute_score failed for {server_data.get("server_id")}: {e}')
        return None


def compute_permission_scope_score(server_data, module):
    if not hasattr(module, 'compute_score'):
        logger.warning('permission_scope_enrichment_v2 has no compute_score')
        return None
    
    metadata = {
        'name': server_data.get('name'),
        'description': server_data.get('description'),
        'url': server_data.get('url'),
        'registry_source': server_data.get('registry_source'),
    }
    
    try:
        result = module.compute_score(metadata)
        return result
    except Exception as e:
        logger.debug(f'compute_score failed for {server_data.get("server_id")}: {e}')
        return None


def write_enrichment_rows(enrichments, signal_type):
    if not enrichments:
        return 0
    
    rows_to_write = []
    for enrich in enrichments:
        rows_to_write.append({
            'server_id': enrich['server_id'],
            'signal_type': signal_type,
            'score': enrich['score'],
            'evidence': enrich.get('evidence', {}),
            'computed_at': utc_now_iso(),
        })
    
    try:
        ws_write('mcp_signal_enrichments', rows_to_write)
        return len(rows_to_write)
    except Exception as e:
        logger.error(f'Failed to write enrichments for {signal_type}: {e}')
        return 0


def process_enrichment_cycle(modules):
    results = {
        'temporal_stability': 0,
        'tool_description_safety': 0,
        'permission_scope': 0,
    }
    
    if 'temporal_stability' in modules:
        module = modules['temporal_stability']
        servers = get_unenriched_servers('temporal_stability')
        
        if servers:
            enrichments = []
            for server in servers:
                score_data = compute_temporal_stability_score(server, module)
                if score_data is not None and score_data.get('score') is not None:
                    enrichments.append({
                        'server_id': server['server_id'],
                        'score': float(score_data['score']),
                        'evidence': score_data.get('evidence', {}),
                    })
            
            if enrichments:
                count = write_enrichment_rows(enrichments, 'temporal_stability')
                results['temporal_stability'] = count
                logger.info(f'Temporal stability: wrote {count} enrichments')
    
    if 'tool_description_safety' in modules:
        module = modules['tool_description_safety']
        servers = get_unenriched_servers('tool_description_safety')
        
        if servers:
            enrichments = []
            for server in servers:
                score_data = compute_tool_description_safety_score(server, module)
                if score_data is not None and score_data.get('score') is not None:
                    enrichments.append({
                        'server_id': server['server_id'],
                        'score': float(score_data['score']),
                        'evidence': score_data.get('evidence', {}),
                    })
            
            if enrichments:
                count = write_enrichment_rows(enrichments, 'tool_description_safety')
                results['tool_description_safety'] = count
                logger.info(f'Tool description safety: wrote {count} enrichments')
    
    if 'permission_scope' in modules:
        module = modules['permission_scope']
        servers = get_unenriched_servers('permission_scope')
        
        if servers:
            enrichments = []
            for server in servers:
                score_data = compute_permission_scope_score(server, module)
                if score_data is not None and score_data.get('score') is not None:
                    enrichments.append({
                        'server_id': server['server_id'],
                        'score': float(score_data['score']),
                        'evidence': score_data.get('evidence', {}),
                    })
            
            if enrichments:
                count = write_enrichment_rows(enrichments, 'permission_scope')
                results['permission_scope'] = count
                logger.info(f'Permission scope: wrote {count} enrichments')
    
    return results


def get_enrichment_stats():
    stats = {}
    for signal_type in ['temporal_stability', 'tool_description_safety', 'permission_scope']:
        sql = f"SELECT COUNT(*) as cnt FROM mcp_signal_enrichments WHERE signal_type = '{signal_type}'"
        try:
            rows = ws_query(sql)
            stats[signal_type] = rows[0]['cnt'] if rows else 0
        except Exception as e:
            logger.error(f'Failed to get stats for {signal_type}: {e}')
            stats[signal_type] = -1
    return stats


def ensure_enrichments_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        id INTEGER DEFAULT autoinc,
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE,
        evidence VARCHAR,
        computed_at TIMESTAMP,
        PRIMARY KEY (id)
    )
    """
    try:
        ws_execute(sql)
        logger.info('Ensured mcp_signal_enrichments table exists')
    except Exception as e:
        logger.error(f'Failed to ensure enrichments table: {e}')


def cycle():
    logger.info('Starting enrichment cycle')
    
    modules = load_enrichment_modules()
    
    if not modules:
        logger.error('No enrichment modules loaded, skipping cycle')
        return None
    
    ensure_enrichments_table()
    
    results = process_enrichment_cycle(modules)
    
    stats = get_enrichment_stats()
    
    total_new = sum(results.values())
    meta = {
        'enrichments_written': results,
        'total_enrichments': stats,
        'modules_loaded': list(modules.keys()),
    }
    
    logger.info(f'Cycle complete: {total_new} new enrichments, stats={stats}')
    send_heartbeat(status='running', meta=meta)
    
    return results


def run():
    logger.info(f'Starting {SERVICE_NAME}')
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info('Loading enrichment modules once at startup')
    modules = load_enrichment_modules()
    
    if not modules:
        logger.error('No enrichment modules could be loaded, exiting')
        remove_pid_file()
        sys.exit(1)
    
    logger.info(f'Loaded modules: {list(modules.keys())}')
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f'Cycle failed with exception: {e}', exc_info=True)
            send_heartbeat(status='error', meta={'error': str(e)})
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()