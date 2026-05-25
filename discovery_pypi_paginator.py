import sys
import os
import time
import re
import json
import threading
import logging
from datetime import datetime
from urllib.parse import quote_plus
import urllib.request
import urllib.error
import ssl

sys.path.insert(0, '/home/workspace/zo_sentinel')
from db_utils import ws_query, ws_write

SERVICE_NAME = 'discovery_pypi_paginator'
SERVICE_PORT = None
PID_FILE = '/tmp/discovery_pypi_paginator.pid'
LOG_FILE = '/var/log/zo_sentinel/discovery_pypi_paginator.log'
STATE_DIR = '/var/lib/zo_sentinel'
STATE_FILE = os.path.join(STATE_DIR, 'pypi_paginator_state.json')

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'

POLL_SECS = 1800
HEARTBEAT_INTERVAL = 60
MAX_REQUESTS_PER_SEC = 1
REQUEST_DELAY = 1.1
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0

PYPI_SIMPLE_URL = 'https://pypi.org/simple/'
PYPI_SEARCH_URL = 'https://pypi.org/search/?q='
PYPI_JSON_API_URL = 'https://pypi.org/pypi/{package}/json'
PYPI_STATS_URL = 'https://pypistats.org/packages/{package}/json'

KEYWORD_FILTERS = ['mcp-server', 'mcp_server', 'model-context-protocol', 'mcp-tool', 'mcp-server-', 'mcp_', '-mcp']
MCP_NAME_PATTERNS = [r'mcp[-_]?server', r'model[-_]?context[-_]?protocol', r'mcp[-_]?tool', r'mcp[-_]?client', r'mcp[-_]?sdk']

_log_handler = None
_logger = None

def setup_logging():
    global _log_handler, _logger
    if _logger is not None:
        return _logger
    _logger = logging.getLogger(SERVICE_NAME)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    if _log_handler is None:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        _log_handler = logging.FileHandler(LOG_FILE)
        _log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        _logger.addHandler(_log_handler)
    return _logger

def log(msg):
    setup_logging().info(msg)

def check_single_instance():
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing_pid = f.read().strip()
        if existing_pid and existing_pid != pid:
            try:
                os.kill(int(existing_pid), 0)
                log(f'Instance already running with PID {existing_pid}')
                sys.exit(0)
            except (OSError, ValueError):
                pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    log(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)

def get_utc_now():
    return datetime.utcnow()

def get_iso_timestamp():
    return get_utc_now().strftime('%Y-%m-%dT%H:%M:%S')

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'last_processed_packages': [], 'last_run': None, 'offset': 0}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_with_retry(url, max_retries=MAX_RETRIES, timeout=15):
    backoff = INITIAL_BACKOFF
    for attempt in range(max_retries):
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={'User-Agent': 'ZO-Sentinel/1.0 (MCP Security Scanner)', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                content = response.read().decode('utf-8')
                content_type = response.headers.get('Content-Type', '')
                if 'html' in content_type.lower():
                    return content, 'html'
                return content, 'json'
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get('Retry-After', str(int(backoff)))
                try:
                    wait_time = int(retry_after)
                except ValueError:
                    wait_time = int(backoff)
                log(f'Rate limited by PyPI, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})')
                time.sleep(min(wait_time, 60))
                backoff *= 2
            elif e.code == 404:
                return None, 'not_found'
            else:
                log(f'HTTP error {e.code} for {url}, attempt {attempt + 1}/{max_retries}')
                time.sleep(backoff)
                backoff *= 2
        except (urllib.error.URLError, Exception) as e:
            log(f'URL error for {url}: {e}, attempt {attempt + 1}/{max_retries}')
            time.sleep(backoff)
            backoff *= 2
    log(f'Failed to fetch {url} after {max_retries} attempts')
    return None, None

def is_mcp_package(package_name):
    name_lower = package_name.lower()
    for pattern in MCP_NAME_PATTERNS:
        if re.search(pattern, name_lower):
            return True
    name_parts = re.split(r'[-_]', name_lower)
    for kw in KEYWORD_FILTERS:
        kw_clean = re.sub(r'[-_]', '', kw)
        for part in name_parts:
            part_clean = re.sub(r'[-_]', '', part)
            if part_clean == kw_clean or kw_clean in part_clean:
                return True
    return False

def fetch_pypi_simple_index():
    log('Fetching PyPI simple package index...')
    content, content_type = get_with_retry(PYPI_SIMPLE_URL, timeout=30)
    if content is None:
        return []
    packages = []
    for line in content.split('\n'):
        line = line.strip()
        if line and line.endswith('/'):
            pkg_name = line.rstrip('/').lower()
            if is_mcp_package(line.rstrip('/')):
                packages.append(line.rstrip('/'))
    log(f'Found {len(packages)} MCP-related packages in simple index')
    return packages

def fetch_package_metadata(package_name):
    url = PYPI_JSON_API_URL.format(package=quote_plus(package_name))
    content, content_type = get_with_retry(url, timeout=20)
    if content is None or content_type == 'not_found':
        return None
    try:
        data = json.loads(content)
        info = data.get('info', {})
        urls = data.get('urls', [])
        latest_version = info.get('version', 'unknown')
        description = info.get('summary', '') or info.get('description', '') or ''
        if len(description) > 1000:
            description = description[:1000]
        author = info.get('author', '') or ''
        author_email = info.get('author_email', '') or ''
        homepage = info.get('home_page', '') or info.get('project_url', '') or info.get('package_url', '') or ''
        license_type = info.get('license', '') or ''
        python_version = info.get('requires_python', '') or ''
        latest_upload_date = None
        if urls:
            urls.sort(key=lambda x: x.get('upload_time', ''), reverse=True)
            latest_upload_date = urls[0].get('upload_time', '')
        return {
            'name': info.get('name', package_name),
            'description': description,
            'version': latest_version,
            'author': author,
            'author_email': author_email,
            'homepage': homepage,
            'license': license_type,
            'python_version': python_version,
            'upload_date': latest_upload_date,
            'pypi_url': f'https://pypi.org/project/{package_name}'
        }
    except (json.JSONDecodeError, Exception) as e:
        log(f'Failed to parse metadata for {package_name}: {e}')
        return None

def get_existing_server_names():
    log('Querying existing server names from registry...')
    try:
        result = ws_query(QUERY_SERVICE_URL, {'sql': "SELECT name FROM mcp_server_registry WHERE name IS NOT NULL"})
        rows = result.get('rows', []) if result else []
        names = {row.get('name', '').lower() for row in rows if row.get('name')}
        log(f'Found {len(names)} existing server names in registry')
        return names
    except Exception as e:
        log(f'Error querying existing servers: {e}')
        return set()

def write_discovery_candidates(candidates):
    if not candidates:
        return 0
    log(f'Writing {len(candidates)} candidates to mcp_discovery_candidates...')
    try:
        rows_to_write = []
        for candidate in candidates:
            rows_to_write.append({
                'name': candidate.get('name', ''),
                'url': candidate.get('pypi_url', f"https://pypi.org/project/{candidate.get('name', '')}"),
                'description': candidate.get('description', ''),
                'discovery_source': 'pypi',
                'discovery_date': get_iso_timestamp(),
                'metadata_json': json.dumps({
                    'version': candidate.get('version', ''),
                    'author': candidate.get('author', ''),
                    'author_email': candidate.get('author_email', ''),
                    'homepage': candidate.get('homepage', ''),
                    'license': candidate.get('license', ''),
                    'python_version': candidate.get('python_version', ''),
                    'upload_date': candidate.get('upload_date', '')
                }),
                'status': 'candidate'
            })
        ws_write(WRITE_SERVICE_URL, {'table': 'mcp_discovery_candidates', 'rows': rows_to_write, 'wait': True})
        log(f'Successfully wrote {len(candidates)} candidates')
        return len(candidates)
    except Exception as e:
        log(f'Error writing candidates: {e}')
        return 0

def heartbeat_loop():
    def heartbeat():
        while True:
            try:
                ws_write(WRITE_SERVICE_URL, {
                    'table': 'service_health',
                    'rows': [{'service': SERVICE_NAME, 'last_heartbeat': get_iso_timestamp()}],
                    'wait': True
                })
            except Exception as e:
                log(f'Heartbeat error: {e}')
            time.sleep(HEARTBEAT_INTERVAL)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    return thread

def cycle():
    log('Starting PyPI discovery cycle...')
    state = load_state()
    existing_names = get_existing_server_names()
    candidates_to_write = []
    packages_processed = 0
    packages_skipped = 0
    packages_found = 0

    mcp_packages = fetch_pypi_simple_index()
    packages_found = len(mcp_packages)
    log(f'Processing {packages_found} MCP-related packages from PyPI...')

    for i, package_name in enumerate(mcp_packages):
        if package_name.lower() in existing_names:
            packages_skipped += 1
            continue
        metadata = fetch_package_metadata(package_name)
        if metadata:
            candidates_to_write.append(metadata)
        packages_processed += 1
        if packages_processed % 10 == 0:
            log(f'Processed {packages_processed}/{packages_found} packages, found {len(candidates_to_write)} candidates')
        time.sleep(REQUEST_DELAY)

    rows_written = 0
    if candidates_to_write:
        rows_written = write_discovery_candidates(candidates_to_write)

    state['last_run'] = get_iso_timestamp()
    state['packages_processed'] = packages_processed
    state['packages_found'] = packages_found
    state['packages_skipped'] = packages_skipped
    state['rows_written'] = rows_written
    save_state(state)

    log(f'PyPI discovery cycle complete: {packages_found} found, {packages_processed} processed, {packages_skipped} skipped (already registered), {rows_written} written')

def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    check_single_instance()
    log(f'Starting {SERVICE_NAME} daemon (PID: {os.getpid()})')

    heartbeat_loop()

    while True:
        try:
            cycle()
        except Exception as e:
            log(f'Error in discovery cycle: {e}')
        log(f'Sleeping for {POLL_SECS}s until next cycle')
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()