#!/usr/bin/env python3
"""
mcp_directory_ingestor.py - Scrape 4 MCP directories to seed mcp_discovery_candidates
"""
import asyncio
import signal
import sys
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

sys.path.insert(0, '/home/workspace/zo_sentinel')

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/zo_sentinel/logs/mcp_directory_ingestor.log'),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger('mcp_directory_ingestor')

SERVICE_NAME = 'mcp_directory_ingestor'
PORT = 8786
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 86400
REQUEST_DELAY = 1.0
USER_AGENT = 'ZO-Sentinel/1.0 (trust-intelligence-platform)'


def check_single_instance() -> bool:
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        import os
        if hasattr(os, 'kill'):
            os.kill(pid, 0)
            LOG.error(f"Another instance already running with PID {pid}")
            return False
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    return True


def write_pid():
    with open(PID_FILE, 'w') as f:
        f.write(str(__import__('os').getpid()))


def remove_pid_file():
    try:
        import os
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def signal_handler(signum, frame):
    LOG.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        return resp.status_code == 200 and resp.json().get('ok', False)
    except Exception as e:
        LOG.error(f"write_service error: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        return []
    except Exception as e:
        LOG.error(f"query error: {e}")
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        return resp.status_code == 200 and resp.json().get('ok', False)
    except Exception as e:
        LOG.error(f"execute error: {e}")
        return False


def send_heartbeat():
    try:
        requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'service_health',
                'rows': [{'service': SERVICE_NAME, 'last_heartbeat': datetime.utcnow().isoformat()}],
                'wait': True
            },
            timeout=10
        )
    except Exception as e:
        LOG.error(f"Heartbeat failed: {e}")


def ensure_candidates_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_discovery_candidates (
        candidate_id INTEGER PRIMARY KEY,
        candidate_name VARCHAR,
        candidate_url VARCHAR,
        candidate_description VARCHAR,
        discovered_in_directory VARCHAR,
        discovered_status VARCHAR DEFAULT 'active',
        promoted BOOLEAN DEFAULT false,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        discovery_metadata VARCHAR
    )
    """
    ws_execute(sql)


def safe_request(url: str, headers: Dict[str, str] = None, params: Dict = None, timeout: int = 30) -> Optional[Dict]:
    try:
        default_headers = {'User-Agent': USER_AGENT}
        if headers:
            default_headers.update(headers)
        resp = requests.get(url, headers=default_headers, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            LOG.warning(f"Rate limited on {url}, waiting 5s")
            time.sleep(5)
            return safe_request(url, headers, params, timeout)
        else:
            LOG.warning(f"HTTP {resp.status_code} on {url}")
            return None
    except Exception as e:
        LOG.error(f"Request failed for {url}: {e}")
        return None


def scrape_pulsemcp() -> tuple:
    LOG.info("Starting PulseMCP scrape")
    source = 'PulseMCP'
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    candidates = []
    offset = 0
    limit = 100
    
    while True:
        url = f"https://api.pulsemcp.com/v0beta/servers?limit={limit}&offset={offset}"
        data = safe_request(url)
        if not data:
            LOG.warning(f"PulseMCP: No data at offset {offset}")
            break
        
        items = data.get('data', []) or data.get('servers', []) or data.get('results', []) or []
        if not items:
            items = data if isinstance(data, list) else []
        
        if not items:
            break
        
        for item in items:
            name = item.get('name', '')
            url_val = item.get('github_url') or item.get('url', '')
            description = item.get('description', '')
            
            if name and url_val:
                candidates.append({
                    'candidate_name': name,
                    'candidate_url': url_val,
                    'candidate_description': description or '',
                    'discovered_in_directory': source,
                    'discovered_status': 'active',
                    'promoted': False,
                    'first_seen': datetime.utcnow().isoformat(),
                    'last_seen': datetime.utcnow().isoformat()
                })
        
        total_fetched += len(items)
        LOG.info(f"PulseMCP: fetched page offset={offset}, count={len(items)}")
        
        if len(items) < limit:
            break
        
        offset += limit
        time.sleep(REQUEST_DELAY)
    
    if candidates:
        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            if ws_write('mcp_discovery_candidates', batch):
                total_inserted += len(batch)
            else:
                total_skipped += len(batch)
            time.sleep(REQUEST_DELAY)
    
    LOG.info(f"PulseMCP complete: fetched={total_fetched}, inserted={total_inserted}, skipped={total_skipped}")
    return total_fetched, total_inserted, total_skipped


def scrape_glama() -> tuple:
    LOG.info("Starting Glama.ai scrape")
    source = 'Glama.ai'
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    candidates = []
    cursor = None
    first = 100
    
    for page in range(50):
        params = {'first': first}
        if cursor:
            params['after'] = cursor
        
        url = "https://glama.ai/api/mcp/v1/servers"
        data = safe_request(url, params=params)
        
        if not data:
            LOG.warning(f"Glama.ai: No data at page {page}")
            break
        
        items = data.get('data', []) or data.get('servers', []) or data.get('edges', [])
        
        if not items:
            items = data if isinstance(data, list) else []
            if not items:
                break
        
        page_items = []
        for item in items:
            if isinstance(item, dict) and 'node' in item:
                item = item['node']
            name = item.get('name', '')
            repo_url = item.get('repositoryUrl', '') or item.get('repoUrl', '') or item.get('url', '')
            description = item.get('description', '')
            
            if name and repo_url:
                candidates.append({
                    'candidate_name': name,
                    'candidate_url': repo_url,
                    'candidate_description': description or '',
                    'discovered_in_directory': source,
                    'discovered_status': 'active',
                    'promoted': False,
                    'first_seen': datetime.utcnow().isoformat(),
                    'last_seen': datetime.utcnow().isoformat()
                })
                page_items.append(name)
        
        total_fetched += len(page_items)
        LOG.info(f"Glama.ai: fetched page {page}, count={len(page_items)}")
        
        cursor = data.get('pageInfo', {}).get('endCursor') if isinstance(data, dict) else None
        if not cursor:
            break
        
        time.sleep(REQUEST_DELAY)
    
    if candidates:
        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            if ws_write('mcp_discovery_candidates', batch):
                total_inserted += len(batch)
            else:
                total_skipped += len(batch)
            time.sleep(REQUEST_DELAY)
    
    LOG.info(f"Glama.ai complete: fetched={total_fetched}, inserted={total_inserted}, skipped={total_skipped}")
    return total_fetched, total_inserted, total_skipped


def scrape_mcpso() -> tuple:
    LOG.info("Starting mcp.so scrape")
    source = 'mcp.so'
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    candidates = []
    
    url = "https://mcp.so/api/servers"
    data = safe_request(url)
    
    if data:
        items = data if isinstance(data, list) else data.get('data', []) or data.get('servers', []) or []
        
        for item in items:
            name = item.get('name', '')
            repo_url = item.get('repo', '') or item.get('repository', '') or item.get('url', '') or item.get('github', '')
            description = item.get('description', '')
            
            if name and repo_url:
                candidates.append({
                    'candidate_name': name,
                    'candidate_url': repo_url,
                    'candidate_description': description or '',
                    'discovered_in_directory': source,
                    'discovered_status': 'active',
                    'promoted': False,
                    'first_seen': datetime.utcnow().isoformat(),
                    'last_seen': datetime.utcnow().isoformat()
                })
        
        total_fetched = len(candidates)
        LOG.info(f"mcp.so API: fetched {total_fetched} servers")
    else:
        LOG.info("mcp.so API not available, attempting page scrape")
        page_data = safe_request("https://mcp.so/servers")
        if page_data:
            LOG.info(f"mcp.so page scrape returned data")
    
    if candidates:
        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            if ws_write('mcp_discovery_candidates', batch):
                total_inserted += len(batch)
            else:
                total_skipped += len(batch)
            time.sleep(REQUEST_DELAY)
    
    LOG.info(f"mcp.so complete: fetched={total_fetched}, inserted={total_inserted}, skipped={total_skipped}")
    return total_fetched, total_inserted, total_skipped


def scrape_smithery() -> tuple:
    LOG.info("Starting Smithery.ai scrape")
    source = 'Smithery.ai'
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    candidates = []
    offset = 0
    limit = 100
    
    for page in range(100):
        url = f"https://smithery.ai/api/v1/servers?q=&limit={limit}&offset={offset}"
        data = safe_request(url)
        
        if not data:
            LOG.warning(f"Smithery.ai: No data at offset {offset}")
            break
        
        items = data if isinstance(data, list) else data.get('data', []) or data.get('servers', []) or data.get('results', [])
        
        if not items:
            break
        
        for item in items:
            qualified_name = item.get('qualifiedName', '')
            display_name = item.get('displayName', item.get('name', ''))
            deployment_url = item.get('deploymentUrl', item.get('url', ''))
            description = item.get('description', '')
            
            name = display_name or qualified_name
            url_val = deployment_url
            
            if name and url_val:
                candidates.append({
                    'candidate_name': name,
                    'candidate_url': url_val,
                    'candidate_description': description or '',
                    'discovered_in_directory': source,
                    'discovered_status': 'active',
                    'promoted': False,
                    'first_seen': datetime.utcnow().isoformat(),
                    'last_seen': datetime.utcnow().isoformat()
                })
        
        total_fetched += len(items)
        LOG.info(f"Smithery.ai: fetched page offset={offset}, count={len(items)}")
        
        if len(items) < limit:
            break
        
        offset += limit
        time.sleep(REQUEST_DELAY)
    
    if candidates:
        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            if ws_write('mcp_discovery_candidates', batch):
                total_inserted += len(batch)
            else:
                total_skipped += len(batch)
            time.sleep(REQUEST_DELAY)
    
    LOG.info(f"Smithery.ai complete: fetched={total_fetched}, inserted={total_inserted}, skipped={total_skipped}")
    return total_fetched, total_inserted, total_skipped


def run_discovery_cycle() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("Starting MCP directory discovery cycle")
    
    ensure_candidates_table()
    
    results = {
        'sources': {},
        'total_fetched': 0,
        'total_inserted': 0,
        'total_skipped': 0,
        'errors': []
    }
    
    sources = [
        ('PulseMCP', scrape_pulsemcp),
        ('Glama.ai', scrape_glama),
        ('mcp.so', scrape_mcpso),
        ('Smithery.ai', scrape_smithery)
    ]
    
    for source_name, scraper_func in sources:
        try:
            fetched, inserted, skipped = scraper_func()
            results['sources'][source_name] = {
                'fetched': fetched,
                'inserted': inserted,
                'skipped': skipped
            }
            results['total_fetched'] += fetched
            results['total_inserted'] += inserted
            results['total_skipped'] += skipped
        except Exception as e:
            LOG.error(f"Error in {source_name} scraper: {e}")
            results['errors'].append({'source': source_name, 'error': str(e)})
            results['sources'][source_name] = {
                'fetched': 0,
                'inserted': 0,
                'skipped': 0,
                'error': str(e)
            }
    
    LOG.info("=" * 60)
    LOG.info("Discovery cycle complete")
    LOG.info(f"Total fetched: {results['total_fetched']}")
    LOG.info(f"Total inserted: {results['total_inserted']}")
    LOG.info(f"Total skipped: {results['total_skipped']}")
    for source, stats in results['sources'].items():
        LOG.info(f"  {source}: fetched={stats['fetched']}, inserted={stats['inserted']}")
    LOG.info("=" * 60)
    
    return results


def get_candidate_count() -> int:
    rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_discovery_candidates")
    if rows:
        return rows[0].get('cnt', 0)
    return 0


def run():
    LOG.info(f"Starting {SERVICE_NAME} daemon")
    
    if not check_single_instance():
        LOG.error("Failed to acquire lock, exiting")
        sys.exit(1)
    
    write_pid()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_candidates_table()
    
    count_before = get_candidate_count()
    LOG.info(f"Candidates before cycle: {count_before}")
    
    run_discovery_cycle()
    
    count_after = get_candidate_count()
    LOG.info(f"Candidates after cycle: {count_after}")
    LOG.info(f"Net new candidates: {count_after - count_before}")
    
    last_heartbeat = time.time()
    cycle_time = time.time()
    
    try:
        while True:
            now = time.time()
            
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
            
            if now - cycle_time >= CYCLE_INTERVAL:
                LOG.info("Starting scheduled discovery cycle")
                run_discovery_cycle()
                cycle_time = now
            
            time.sleep(30)
    
    except Exception as e:
        LOG.error(f"Daemon error: {e}")
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()