import os
import sys
import signal
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

sys.path.insert(0, '/home/workspace')
sys.path.insert(0, '/home/workspace/zo_sentinel')

from db_utils import ws_query, ws_write

SERVICE_NAME = 'supply_chain_enrichment_integration'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60
POLL_SECS = 300
_last_heartbeat = None

_running = True

try:
    from supply_chain_enrichment import compute_score as compute_supply_chain_score
    ENRICHMENT_MODULE_LOADED = True
    log.info("supply_chain_enrichment module loaded successfully")
except ImportError as e:
    ENRICHMENT_MODULE_LOADED = False
    log.warning(f"Could not import supply_chain_enrichment: {e}")
    log.warning("Will query mcp_signal_enrichments table directly")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"{SERVICE_NAME} already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.info(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f"{SERVICE_NAME} started with PID {pid}")


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
        log.info("PID file removed")


def signal_handler(signum, frame):
    global _running
    log.info(f"Received signal {signum}, shutting down gracefully")
    _running = False


def send_heartbeat():
    global _last_heartbeat
    now = utc_now_iso()
    _last_heartbeat = now
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': now,
            'status': 'ok',
            'meta': f'enrichment_module_loaded={ENRICHMENT_MODULE_LOADED}'
        }])
    except Exception as e:
        log.warning(f"Failed to send heartbeat: {e}")


def get_unprocessed_servers(batch_size: int = 50) -> List[Dict[str, Any]]:
    """
    Query for servers that have base supply_chain signal but may need
    enrichment layer integration. Checks mcp_signal_scores for supply_chain
    and mcp_signal_enrichments for existing enrichment records.
    """
    query = """
    SELECT DISTINCT sr.server_id, sr.name, sr.registry_source, sr.first_seen, sr.last_seen
    FROM mcp_server_registry sr
    WHERE sr.server_id IS NOT NULL
    LIMIT ?
    """
    try:
        result = ws_query(query, params=[batch_size])
        if result and result.get('rows'):
            return result['rows']
    except Exception as e:
        log.error(f"Failed to query unprocessed servers: {e}")
    return []


def get_existing_enrichment(server_id: str) -> Optional[Dict[str, Any]]:
    """Query mcp_signal_enrichments table for existing supply_chain_enrichment row."""
    query = """
    SELECT signal_type, score, evidence, computed_at
    FROM mcp_signal_enrichments
    WHERE server_id = ? AND signal_type = 'supply_chain_enrichment'
    ORDER BY computed_at DESC
    LIMIT 1
    """
    try:
        result = ws_query(query, params=[server_id])
        if result and result.get('rows') and len(result['rows']) > 0:
            return result['rows'][0]
    except Exception as e:
        log.debug(f"No existing enrichment for {server_id}: {e}")
    return None


def get_base_signal_score(server_id: str) -> Optional[float]:
    """Query mcp_signal_scores for base supply_chain signal score."""
    query = """
    SELECT score FROM mcp_signal_scores
    WHERE server_id = ? AND signal_name = 'supply_chain_signal'
    LIMIT 1
    """
    try:
        result = ws_query(query, params=[server_id])
        if result and result.get('rows') and len(result['rows']) > 0:
            return float(result['rows'][0].get('score', 0))
    except Exception as e:
        log.debug(f"No base signal for {server_id}: {e}")
    return None


def get_server_metadata(server_id: str) -> Dict[str, Any]:
    """Query mcp_ecosystems_metadata for enrichment metadata fields."""
    query = """
    SELECT registry_source, age_days, download_count, dependency_count,
           publisher_verified, stars, last_scanned
    FROM mcp_ecosystems_metadata
    WHERE server_id = ?
    LIMIT 1
    """
    try:
        result = ws_query(query, params=[server_id])
        if result and result.get('rows') and len(result['rows']) > 0:
            row = result['rows'][0]
            metadata = {
                'registry_source': row.get('registry_source'),
                'age_days': int(row.get('age_days', 0)) if row.get('age_days') else None,
                'download_count': int(row.get('download_count', 0)) if row.get('download_count') else None,
                'dependency_count': int(row.get('dependency_count', 0)) if row.get('dependency_count') else None,
                'publisher_verified': bool(row.get('publisher_verified')) if row.get('publisher_verified') is not None else None,
                'stars': int(row.get('stars', 0)) if row.get('stars') else None
            }
            return metadata
    except Exception as e:
        log.debug(f"No metadata for {server_id}: {e}")
    return {}


def compute_enrichment_score(metadata: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
    """
    Compute supply chain enrichment score using the enrichment module.
    Falls back to metadata-based scoring if module not loaded.
    """
    if ENRICHMENT_MODULE_LOADED:
        try:
            score, evidence = compute_supply_chain_score(metadata)
            return score, evidence
        except Exception as e:
            log.warning(f"Enrichment module compute_score failed: {e}")
    
    score = 50.0
    evidence = {'fallback': True}
    
    registry = metadata.get('registry_source', '')
    if registry in ['npm', 'npmjs', 'pypi', 'PyPI', 'crates.io']:
        score += 10
        evidence['registry_trusted'] = registry
    elif registry:
        score -= 5
        evidence['registry_unknown'] = registry
    
    age_days = metadata.get('age_days')
    if age_days is not None:
        if age_days > 365:
            score += 15
            evidence['age_mature'] = age_days
        elif age_days < 30:
            score -= 10
            evidence['age_new'] = age_days
    
    downloads = metadata.get('download_count')
    if downloads is not None:
        if downloads > 1000000:
            score += 10
            evidence['downloads_high'] = downloads
        elif downloads < 100:
            score -= 5
            evidence['downloads_low'] = downloads
    
    if metadata.get('publisher_verified'):
        score += 15
        evidence['publisher_verified'] = True
    
    stars = metadata.get('stars')
    if stars is not None:
        if stars > 1000:
            score += 5
            evidence['stars_high'] = stars
        elif stars > 0:
            evidence['stars_present'] = stars
    
    score = max(0.0, min(100.0, score))
    evidence['final_score'] = round(score, 2)
    
    return round(score, 2), evidence


def merge_with_base_signal(enrichment_score: float, base_score: Optional[float]) -> float:
    """
    Merge enrichment score with base supply_chain signal score.
    Uses weighted blending for trust_synthesiser_v2 compatibility.
    """
    if base_score is None:
        return enrichment_score
    
    enrichment_weight = 0.4
    base_weight = 0.6
    
    merged = (enrichment_score * enrichment_weight) + (base_score * base_weight)
    return round(merged, 2)


def write_enrichment_record(server_id: str, enrichment_score: float, 
                            merged_score: float, evidence: Dict[str, Any]):
    """Write enrichment result to mcp_signal_enrichments table."""
    now = utc_now_iso()
    row = {
        'server_id': server_id,
        'signal_type': 'supply_chain_enrichment',
        'score': enrichment_score,
        'merged_score': merged_score,
        'evidence': evidence,
        'computed_at': now,
        'source': 'supply_chain_enrichment_integration'
    }
    try:
        ws_write('mcp_signal_enrichments', [row])
        log.debug(f"Wrote enrichment for {server_id}: score={enrichment_score}")
    except Exception as e:
        log.error(f"Failed to write enrichment for {server_id}: {e}")


def update_signal_scores_merged(server_id: str, merged_score: float):
    """
    Update mcp_signal_scores with the merged supply chain score.
    This integrates with trust_synthesiser_v2 weighting system.
    """
    query = """
    SELECT COUNT(*) as cnt FROM mcp_signal_scores
    WHERE server_id = ? AND signal_name = 'supply_chain_signal'
    """
    try:
        result = ws_query(query, params=[server_id])
        exists = result and result.get('rows') and result['rows'][0].get('cnt', 0) > 0
    except Exception:
        exists = False
    
    now = utc_now_iso()
    if exists:
        update_sql = """
        UPDATE mcp_signal_scores
        SET score = ?, evidence = json_set(COALESCE(evidence, '{}'), '$.enrichment_merged', ?),
            scored_at = ?
        WHERE server_id = ? AND signal_name = 'supply_chain_signal'
        """
        try:
            ws_query(update_sql, params=[merged_score, merged_score, now, server_id])
            log.debug(f"Updated signal score for {server_id}: {merged_score}")
        except Exception as e:
            log.warning(f"Failed to update signal score: {e}")
    else:
        insert_sql = """
        INSERT INTO mcp_signal_scores (server_id, signal_name, score, evidence, scored_at)
        VALUES (?, 'supply_chain_signal', ?, json('{"enrichment_merged": ?}'), ?)
        """
        try:
            ws_query(insert_sql, params=[server_id, merged_score, merged_score, now])
            log.debug(f"Inserted signal score for {server_id}: {merged_score}")
        except Exception as e:
            log.warning(f"Failed to insert signal score: {e}")


def process_server(server: Dict[str, Any]) -> bool:
    """Process a single server for supply chain enrichment integration."""
    server_id = server.get('server_id')
    if not server_id:
        return False
    
    try:
        existing = get_existing_enrichment(server_id)
        if existing:
            log.debug(f"Server {server_id} already has enrichment, skipping")
            return True
        
        metadata = get_server_metadata(server_id)
        if not metadata or not any(metadata.values()):
            log.debug(f"No metadata available for {server_id}")
            return False
        
        enrichment_score, evidence = compute_enrichment_score(metadata)
        
        base_score = get_base_signal_score(server_id)
        merged_score = merge_with_base_signal(enrichment_score, base_score)
        
        write_enrichment_record(server_id, enrichment_score, merged_score, evidence)
        update_signal_scores_merged(server_id, merged_score)
        
        log.info(f"Processed {server_id}: enrichment={enrichment_score}, merged={merged_score}")
        return True
        
    except Exception as e:
        log.error(f"Error processing server {server_id}: {e}")
        return False


def cycle():
    """Process a batch of servers per cycle."""
    global _last_heartbeat
    
    servers = get_unprocessed_servers(batch_size=50)
    if not servers:
        log.info("No servers requiring enrichment processing")
        return
    
    processed = 0
    for server in servers:
        if not _running:
            break
        if process_server(server):
            processed += 1
    
    log.info(f"Cycle complete: processed {processed}/{len(servers)} servers")
    
    now = utc_now_iso()
    if _last_heartbeat is None or ((datetime.now(timezone.utc) - datetime.fromisoformat(_last_heartbeat.replace('Z', '+00:00'))).total_seconds() >= HEARTBEAT_INTERVAL):
        send_heartbeat()


def run():
    """Main daemon loop."""
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"{SERVICE_NAME} starting - wiring supply_chain_enrichment into signal pipeline")
    
    send_heartbeat()
    
    while _running:
        try:
            cycle()
        except Exception as e:
            log.error(f"Cycle error: {e}")
        
        time.sleep(POLL_SECS)
    
    remove_pid_file()
    log.info(f"{SERVICE_NAME} shut down complete")


if __name__ == '__main__':
    import time
    run()