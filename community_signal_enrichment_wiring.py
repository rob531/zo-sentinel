#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/workspace')
import os
import time
import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

SERVICE_NAME = 'community_signal_enrichment_wiring'
SERVICE_PORT = 0
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = f'{QUERY_SERVICE_URL}/query'
WRITE_URL = f'{WRITE_SERVICE_URL}/write'
EXECUTE_URL = f'{EXECUTE_SERVICE_URL}/execute'
LOG_FILE = '/home/workspace/logs/community_signal_enrichment_wiring.log'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

POLL_SECS = 300
BATCH_SIZE = 50
HTTP_TIMEOUT = 10.0
WRITE_TIMEOUT = 30.0
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
SIGNAL_TYPE = 'community_signal'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

import requests


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_URL, json=payload, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def check_single_instance() -> bool:
    pid = str(os.getpid())
    try:
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing != pid:
            log.error(f"Another instance running: {existing}. Exiting.")
            return False
    except FileNotFoundError:
        pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    log.info(f"Acquired PID file: {PID_FILE}")
    return True


def remove_pid_file() -> None:
    try:
        os.unlink(PID_FILE)
        log.info(f"Removed PID file: {PID_FILE}")
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    sig_name = 'SIGTERM' if signum == 15 else 'SIGINT' if signum == 2 else f'signal-{signum}'
    log.warning(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': json.dumps(meta) if meta else '{}'
    }
    try:
        ws_write('service_health', [row])
    except Exception as e:
        log.warning(f"Failed to send heartbeat: {e}")


def ensure_enrichments_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE,
        evidence VARCHAR,
        metadata VARCHAR,
        computed_at TIMESTAMPTZ,
        PRIMARY KEY (server_id, signal_type)
    )
    """
    try:
        ws_execute(create_sql)
        log.info("Ensured mcp_signal_enrichments table exists")
    except Exception as e:
        log.warning(f"Table ensure failed (may already exist): {e}")


def get_servers_missing_community_signal(batch_size: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.registry_source,
           r.metadata_stars, r.metadata_download_count, r.metadata_age_days,
           r.metadata_forks, r.metadata_contributors, r.metadata_verified,
           r.metadata_maintainers
    FROM mcp_server_registry r
    WHERE r.verdict != 'KNOWN_THREAT'
      AND NOT EXISTS (
          SELECT 1 FROM mcp_signal_enrichments e
          WHERE e.server_id = r.server_id
            AND e.signal_type = '{SIGNAL_TYPE}'
      )
    ORDER BY r.trust_score ASC NULLS FIRST
    LIMIT {batch_size}
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.error(f"Failed to query missing servers: {e}")
        return []


def extract_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {
        'name': row.get('name', ''),
        'url': row.get('url', ''),
        'registry_source': row.get('registry_source', 'unknown'),
        'stars': row.get('metadata_stars', 0) or 0,
        'download_count': row.get('metadata_download_count', 0) or 0,
        'age_days': row.get('metadata_age_days', 0) or 0,
        'forks': row.get('metadata_forks', 0) or 0,
        'contributors': row.get('metadata_contributors', 0) or 0,
        'verified': row.get('metadata_verified', False) or False,
        'maintainers': row.get('metadata_maintainers', 0) or 0,
    }
    return metadata


def fetch_npm_metadata(package_name: str) -> Dict[str, Any]:
    if not package_name:
        return {}
    url = f'https://registry.npmjs.org/{package_name}'
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            downloads_url = f'https://api.npmjs.org/downloads/point/last-month/{package_name}'
            downloads_resp = requests.get(downloads_url, timeout=HTTP_TIMEOUT)
            downloads = 0
            if downloads_resp.status_code == 200:
                dl_data = downloads_resp.json()
                downloads = dl_data.get('downloads', 0)
            return {
                'downloads': downloads,
                'version': data.get('latest', {}).get('version') if isinstance(data.get('latest'), dict) else str(data.get('latest', '')),
                'maintainers': len(data.get('maintainers', [])) if data.get('maintainers') else 0,
            }
    except Exception as e:
        log.debug(f"npm metadata fetch failed for {package_name}: {e}")
    return {}


def enrich_with_external_metadata(server_id: str, url: str, current_metadata: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(current_metadata)
    if not url:
        return enriched
    if 'npmjs.com' in url or 'npmjs.org' in url:
        parts = url.split('/')
        if 'package' in parts:
            idx = parts.index('package')
            if idx + 1 < len(parts):
                pkg_name = parts[idx + 1].split('#')[0].split('?')[0]
                npm_data = fetch_npm_metadata(pkg_name)
                if npm_data:
                    if npm_data.get('downloads'):
                        enriched['download_count'] = npm_data['downloads']
                    if npm_data.get('maintainers'):
                        enriched['maintainers'] = npm_data['maintainers']
    return enriched


def write_enrichment(server_id: str, score: float, evidence: str, metadata: Dict[str, Any]) -> bool:
    row = {
        'server_id': server_id,
        'signal_type': SIGNAL_TYPE,
        'score': score,
        'evidence': evidence,
        'metadata': json.dumps(metadata),
        'computed_at': utc_now_iso(),
    }
    for attempt in range(MAX_RETRIES):
        try:
            ws_write('mcp_signal_enrichments', [row])
            return True
        except Exception as e:
            backoff = BACKOFF_BASE ** attempt
            log.warning(f"Write attempt {attempt + 1} failed for {server_id}: {e}. Retrying in {backoff}s")
            time.sleep(backoff)
    log.error(f"Failed to write enrichment for {server_id} after {MAX_RETRIES} attempts")
    return False


def compute_with_backoff(enrichment_module, metadata: Dict[str, Any]) -> tuple:
    for attempt in range(MAX_RETRIES):
        try:
            return enrichment_module.compute_score(metadata)
        except Exception as e:
            backoff = BACKOFF_BASE ** attempt
            log.warning(f"compute_score attempt {attempt + 1} failed: {e}. Retrying in {backoff}s")
            time.sleep(backoff)
    log.error("All compute_score attempts failed")
    return 0.0, "error", {}


def cycle() -> int:
    ensure_enrichments_table()
    servers = get_servers_missing_community_signal()
    if not servers:
        log.debug("No servers missing community_signal enrichment")
        return 0
    log.info(f"Processing {len(servers)} servers for community_signal enrichment")
    try:
        from community_signal_enrichment import compute_score
    except ImportError as e:
        log.error(f"Failed to import community_signal_enrichment: {e}")
        return 0
    processed = 0
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        url = server.get('url', '')
        metadata = extract_metadata(server)
        metadata = enrich_with_external_metadata(server_id, url, metadata)
        try:
            score, evidence = compute_with_backoff(
                __import__('community_signal_enrichment', fromlist=['compute_score']),
                metadata
            )
        except Exception as e:
            log.error(f"compute_score failed for {server_id}: {e}")
            score, evidence = 0.0, f"error: {e}"
        full_metadata = {
            **metadata,
            'score_details': evidence,
            'url': url,
        }
        if write_enrichment(server_id, score, evidence, full_metadata):
            processed += 1
            log.debug(f"Enriched {server_id} with score={score:.2f}")
        else:
            log.error(f"Failed to write enrichment for {server_id}")
    log.info(f"Cycle complete: processed={processed}/{len(servers)}")
    return processed


def run() -> None:
    import signal
    if not check_single_instance():
        sys.exit(1)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"Starting {SERVICE_NAME}")
    try:
        while True:
            start = time.time()
            try:
                count = cycle()
                send_heartbeat('running', {'processed': count})
            except Exception as e:
                log.error(f"Cycle failed: {e}")
                send_heartbeat('error', {'error': str(e)})
            elapsed = time.time() - start
            sleep_time = max(1, POLL_SECS - elapsed)
            log.debug(f"Cycle took {elapsed:.1f}s, sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()