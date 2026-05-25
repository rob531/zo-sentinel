import os
import sys
import time
import hashlib
import logging
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, '/home/workspace/zo_sentinel')
from db_utils import ws_query, ws_write

SERVICE_NAME = "discovery_pypi_paginator_v2"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/var/log/zo_sentinel"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

POLL_SECS = 600
PAGES_PER_CYCLE = 5
RESULTS_PER_PAGE = 100
RATE_LIMIT_CALLS = 60
RATE_LIMIT_WINDOW = 60
SEARCH_KEYWORDS = ["mcp-server", "model-context-protocol", "mcp-tools"]

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
PYPI_API_BASE = "https://pypi.org/search"

call_timestamps: List[float] = []
logger: Optional[logging.Logger] = None


def setup_logging() -> None:
    global logger
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def log(msg: str, level: str = "INFO") -> None:
    if logger:
        getattr(logger, level.lower())(msg)
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"{timestamp} {SERVICE_NAME} [{level}] {msg}", flush=True)


def get_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log(f"Instance already running with PID {old_pid}", "WARNING")
            return False
        except (OSError, ValueError):
            log(f"Stale PID file found, removing", "INFO")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    log(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def rate_limit_call() -> None:
    global call_timestamps
    now = time.time()
    call_timestamps = [ts for ts in call_timestamps if now - ts < RATE_LIMIT_WINDOW]
    if len(call_timestamps) >= RATE_LIMIT_CALLS:
        sleep_time = RATE_LIMIT_WINDOW - (now - call_timestamps[0]) + 0.1
        if sleep_time > 0:
            log(f"Rate limit reached, sleeping {sleep_time:.1f}s", "INFO")
            time.sleep(sleep_time)
        now = time.time()
        call_timestamps = [ts for ts in call_timestamps if now - ts < RATE_LIMIT_WINDOW]
    call_timestamps.append(now)


def compute_server_id(package_name: str) -> str:
    key = f"pypi:{package_name}"
    return hashlib.blake2s(key.encode('utf-8'), digest_size=8).hexdigest()


def search_pypi_json_api(keyword: str, page: int) -> Dict[str, Any]:
    url = "https://pypi.org/search/"
    params = {
        'q': keyword,
        'page': page
    }
    headers = {
        'User-Agent': 'ZO-Sentinel/1.0 (MCP Security Scanner)',
        'Accept': 'application/json'
    }
    rate_limit_call()
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {}
    except requests.exceptions.RequestException as e:
        log(f"Error fetching page {page} for keyword '{keyword}': {e}", "ERROR")
        return {}


def search_pypi_simple_api(package_name: str) -> Optional[Dict[str, Any]]:
    url = f"https://pypi.org/pypi/{package_name}/json"
    headers = {
        'User-Agent': 'ZO-Sentinel/1.0 (MCP Security Scanner)',
        'Accept': 'application/json'
    }
    rate_limit_call()
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.exceptions.RequestException as e:
        log(f"Error fetching package info for '{package_name}': {e}", "ERROR")
        return None


def extract_package_info(package_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        info = package_data.get('info', {})
        name = info.get('name', '')
        version = info.get('version', '0.0.0')
        summary = info.get('summary', '') or ''
        description = info.get('description', '') or ''
        author = info.get('author', '') or ''
        author_email = info.get('author_email', '') or ''
        home_page = info.get('home_page', '') or info.get('project_url', '') or ''
        project_urls = info.get('project_urls', {}) or {}
        repository = project_urls.get('Repository', '') or project_urls.get('Source', '') or ''
        license_type = info.get('license', '') or ''
        keywords = info.get('keywords', '') or ''
        created = info.get('created', '')
        latest_release = info.get('releases', {}).get(info.get('version', ''), [{}])[0].get('upload_time', '') if info.get('releases') else ''
        downloads = info.get('downloads', {}).get('last_week', 0) if isinstance(info.get('downloads'), dict) else 0
        
        return {
            'name': name,
            'version': version,
            'summary': summary[:500] if summary else '',
            'description': description[:2000] if description else '',
            'author': author,
            'author_email': author_email,
            'home_page': home_page,
            'repository': repository,
            'license': license_type,
            'keywords': keywords,
            'created_at': created,
            'latest_release': latest_release,
            'downloads_last_week': downloads
        }
    except Exception as e:
        log(f"Error extracting package info: {e}", "ERROR")
        return None


def ensure_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_discovery_candidates (
        server_id VARCHAR PRIMARY KEY,
        source VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        version VARCHAR,
        description TEXT,
        author VARCHAR,
        author_email VARCHAR,
        url VARCHAR,
        repository VARCHAR,
        license VARCHAR,
        keywords VARCHAR,
        created_at VARCHAR,
        latest_release VARCHAR,
        downloads_last_week BIGINT,
        ingested_at VARCHAR NOT NULL,
        status VARCHAR DEFAULT 'pending',
        metadata JSON
    )
    """
    ws_write(WRITE_SERVICE_URL, sql)


def insert_candidate(candidate: Dict[str, Any]) -> None:
    sql = """
    INSERT INTO mcp_discovery_candidates 
    (server_id, source, name, version, description, author, author_email, url, repository, license, keywords, created_at, latest_release, downloads_last_week, ingested_at, status, metadata)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    ON CONFLICT (server_id) DO NOTHING
    """
    server_id = compute_server_id(candidate['name'])
    ingested_at = get_utc_now()
    metadata = {
        'ingestion_source': 'pypi_paginator_v2',
        'discovered_keywords': candidate.get('keywords', '')
    }
    params = (
        server_id,
        'pypi',
        candidate['name'],
        candidate.get('version', '0.0.0'),
        candidate.get('summary', ''),
        candidate.get('author', ''),
        candidate.get('author_email', ''),
        candidate.get('home_page', ''),
        candidate.get('repository', ''),
        candidate.get('license', ''),
        candidate.get('keywords', ''),
        candidate.get('created_at', ''),
        candidate.get('latest_release', ''),
        candidate.get('downloads_last_week', 0),
        ingested_at,
        str(metadata)
    )
    try:
        ws_write(WRITE_SERVICE_URL, sql, params)
        log(f"Inserted/updated candidate: {candidate['name']}", "DEBUG")
    except Exception as e:
        log(f"Error inserting candidate '{candidate['name']}': {e}", "ERROR")


def process_keyword(keyword: str) -> int:
    log(f"Processing keyword: {keyword}", "INFO")
    total_found = 0
    
    for page in range(1, PAGES_PER_CYCLE + 1):
        log(f"Fetching page {page} for keyword '{keyword}'", "INFO")
        data = search_pypi_json_api(keyword, page)
        
        if not data or 'packages' not in data:
            log(f"No packages found on page {page} for '{keyword}'", "WARNING")
            continue
        
        packages = data.get('packages', []) or []
        log(f"Found {len(packages)} results on page {page} for '{keyword}'", "INFO")
        
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            package_name = pkg.get('name', '')
            if not package_name:
                continue
            
            pkg_info = search_pypi_simple_api(package_name)
            if pkg_info:
                extracted = extract_package_info(pkg_info)
                if extracted:
                    insert_candidate(extracted)
                    total_found += 1
            else:
                simple_candidate = {
                    'name': package_name,
                    'version': pkg.get('version', '0.0.0'),
                    'summary': pkg.get('summary', ''),
                    'description': pkg.get('description', ''),
                    'author': pkg.get('author', ''),
                    'author_email': '',
                    'home_page': pkg.get('home_page', ''),
                    'repository': pkg.get('repository', ''),
                    'license': '',
                    'keywords': keyword,
                    'created_at': '',
                    'latest_release': '',
                    'downloads_last_week': 0
                }
                insert_candidate(simple_candidate)
                total_found += 1
            
            time.sleep(0.1)
    
    return total_found


def cycle() -> int:
    log("Starting PyPI discovery cycle", "INFO")
    total_candidates = 0
    
    ensure_table()
    
    for keyword in SEARCH_KEYWORDS:
        count = process_keyword(keyword)
        total_candidates += count
        log(f"Found {count} candidates for keyword '{keyword}'", "INFO")
    
    log(f"Cycle complete. Total candidates discovered: {total_candidates}", "INFO")
    return total_candidates


def send_heartbeat() -> None:
    sql = """
    INSERT INTO service_health (service, last_heartbeat)
    VALUES (?, ?)
    ON CONFLICT (service) DO UPDATE SET last_heartbeat = excluded.last_heartbeat
    """
    try:
        ws_write(WRITE_SERVICE_URL, sql, (SERVICE_NAME, get_utc_now()))
    except Exception as e:
        log(f"Heartbeat failed: {e}", "ERROR")


def run() -> None:
    setup_logging()
    log(f"Starting {SERVICE_NAME}", "INFO")
    
    if not check_single_instance():
        log("Cannot start: another instance is running", "ERROR")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_table()
    send_heartbeat()
    
    log(f"Service started. Poll interval: {POLL_SECS}s, Pages per cycle: {PAGES_PER_CYCLE}", "INFO")
    log(f"Keywords: {', '.join(SEARCH_KEYWORDS)}", "INFO")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            cycle()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log(f"Error in cycle: {e}", "ERROR")
            if consecutive_errors >= max_consecutive_errors:
                log(f"Too many consecutive errors ({consecutive_errors}), restarting", "CRITICAL")
        
        send_heartbeat()
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()