import sys
import os
import hashlib
import signal
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

sys.path.insert(0, '/home/workspace')
from db_utils import ws_query, ws_write

SERVICE_NAME = 'community_signal_enrichment_integration'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
EXECUTE_URL = 'http://localhost:8772/execute'
QUERY_URL = 'http://localhost:8772/query'
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

POLL_SECS = 60
BATCH_SIZE = 200
DEFAULT_TIMEOUT = 20

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance is running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found. Removing.")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum: int, frame) -> None:
    log.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': 'service_health', 'rows': [payload]},
            timeout=DEFAULT_TIMEOUT
        )
        if resp.status_code not in (200, 201):
            log.warning(f"Heartbeat failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"Heartbeat error: {e}")

def ws_execute(sql: str) -> None:
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()

def query_enrichment_rows(signal_type: str = 'community_signal_enrichment', limit: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, signal_name, score, evidence, computed_at
    FROM mcp_signal_enrichments
    WHERE signal_name = %s
    ORDER BY computed_at DESC
    LIMIT %s
    """
    payload = {'sql': sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])

def query_servers_for_enrichment(limit: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    sql = """
    SELECT r.server_id, r.name, r.url, r.description, r.registry_source,
           r.stars, r.download_count, r.downloads, r.weekly_downloads,
           r.age_days, r.publisher_verified, r.dependency_count,
           r.forks, r.open_issues, r.contributors, r.commit_activity,
           r.version, r.first_seen, r.last_seen,
           e.score AS enrichment_score, e.evidence AS enrichment_evidence
    FROM mcp_server_registry r
    LEFT JOIN mcp_signal_enrichments e
           ON r.server_id = e.server_id
          AND e.signal_name = 'community_signal_enrichment'
    WHERE r.verdict != 'unknown'
    ORDER BY r.last_seen DESC
    LIMIT %s
    """
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get('rows', [])

def merge_enrichment_into_signal(base_score: float, enrichment_score: float, enrichment_evidence: Optional[Dict] = None) -> float:
    """
    Merge community_signal_enrichment score with base community_signal score.
    Per spec: enrichment layer uses softmax weighting with the base signal.
    Enrichment gets 0.6 weight; base signal gets 0.4 weight (enrichment is higher quality).
    """
    ENRICHMENT_WEIGHT = 0.6
    BASE_WEIGHT = 0.4
    merged = (base_score * BASE_WEIGHT) + (enrichment_score * ENRICHMENT_WEIGHT)
    log.debug(f"Merged: base={base_score} * {BASE_WEIGHT} + enrichment={enrichment_score} * {ENRICHMENT_WEIGHT} = {merged:.4f}")
    return merged

def compute_base_community_score(server: Dict[str, Any]) -> float:
    """
    Compute base community_signal score from registry metadata.
    Mirrors the logic from community_signal_enrichment.py for the base signal layer.
    """
    score_components = []
    
    stars = server.get('stars') or server.get('stargazers_count')
    if stars is not None:
        stars_score = min(100.0, (int(stars) / 500) * 100)
        score_components.append(stars_score * 0.25)
    
    downloads = (server.get('download_count') or server.get('weekly_downloads') or server.get('downloads') or 0)
    if downloads:
        dl_score = min(100.0, (int(downloads) / 50000) * 100)
        score_components.append(dl_score * 0.25)
    
    registry_source = server.get('registry_source', '').lower()
    if registry_source in ('github', 'npm'):
        score_components.append(25.0 * 0.20)
    
    age_days = server.get('age_days')
    if age_days is not None:
        age_score = min(100.0, (int(age_days) / 365) * 100)
        score_components.append(age_score * 0.15)
    
    verified = server.get('publisher_verified')
    if verified:
        score_components.append(100.0 * 0.15)
    
    return sum(score_components) if score_components else 0.0

def write_enriched_signal_score(server_id: str, base_score: float, enrichment_score: Optional[float],
                                  merged_score: float, evidence: Dict[str, Any]) -> None:
    signal_name = 'community_signal'
    evidence_str = None
    if evidence:
        try:
            evidence_str = __import__('json').dumps(evidence)
        except Exception:
            evidence_str = str(evidence)
    
    deterministic_id = hashlib.sha256(f"{server_id}:{signal_name}".encode()).hexdigest()[:32]
    computed_at = utc_now_iso()
    
    payload = {
        'server_id': server_id,
        'signal_name': signal_name,
        'score': round(merged_score, 4),
        'evidence': evidence_str,
        'scored_at': computed_at
    }
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': 'mcp_signal_scores', 'rows': [payload]},
            timeout=DEFAULT_TIMEOUT
        )
        if resp.status_code in (200, 201):
            log.debug(f"Wrote signal score for {server_id}: {merged_score:.4f}")
        else:
            log.warning(f"Failed to write signal score for {server_id}: {resp.text}")
    except Exception as e:
        log.error(f"Error writing signal score for {server_id}: {e}")

def cycle() -> Dict[str, Any]:
    """
    One unit of work: fetch servers, apply enrichment, write merged scores.
    """
    stats = {'processed': 0, 'enriched': 0, 'errors': 0}
    
    try:
        servers = query_servers_for_enrichment(limit=BATCH_SIZE)
        if not servers:
            log.info("No servers found for enrichment cycle.")
            return stats
        
        log.info(f"Processing {len(servers)} servers for community_signal enrichment.")
        
        for server in servers:
            try:
                server_id = server.get('server_id')
                if not server_id:
                    continue
                
                base_score = compute_base_community_score(server)
                
                enrichment_score = server.get('enrichment_score')
                enrichment_evidence = server.get('enrichment_evidence')
                
                if enrichment_score is not None:
                    merged = merge_enrichment_into_signal(base_score, float(enrichment_score), enrichment_evidence)
                    stats['enriched'] += 1
                else:
                    merged = base_score
                
                evidence = {
                    'source': 'community_signal_enrichment_integration',
                    'version': '1.0.0',
                    'base_score': round(base_score, 4),
                    'enrichment_score': round(float(enrichment_score), 4) if enrichment_score is not None else None,
                    'merged_score': round(merged, 4),
                    'enrichment_evidence': enrichment_evidence,
                    'computed_at': utc_now_iso()
                }
                
                write_enriched_signal_score(server_id, base_score, enrichment_score, merged, evidence)
                stats['processed'] += 1
                
            except Exception as e:
                log.error(f"Error processing server {server.get('server_id', 'unknown')}: {e}")
                stats['errors'] += 1
        
        log.info(f"Cycle complete: processed={stats['processed']}, enriched={stats['enriched']}, errors={stats['errors']}")
        
    except Exception as e:
        log.error(f"Cycle error: {e}")
        stats['errors'] += 1
    
    return stats

def run() -> None:
    log.info(f"Starting {SERVICE_NAME}...")
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Community signal enrichment integration daemon running. Poll interval: {POLL_SECS}s")
    
    while True:
        try:
            stats = cycle()
            send_heartbeat('running', stats)
        except Exception as e:
            log.error(f"Run loop error: {e}")
            send_heartbeat('error', {'error': str(e)})
        
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()