import requests
import json
import datetime
import threading
import time
import os
import sys
from pathlib import Path

SERVICE_NAME = 'discovery_github_paginator'
SERVICE_PORT = 8782
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
STATE_DIR = Path('/home/workspace/zo_sentinel/state')
STATE_FILE = STATE_DIR / 'github_pagination_cursor.json'
LOCK_FILE = Path('/home/workspace/logs/discovery_github_paginator.lock')
HEARTBEAT_INTERVAL = 30
CYCLE_INTERVAL = 1800
MAX_PAGES = 10
PER_PAGE = 100
BASE_URL = 'https://api.github.com/search/repositories'
HEADERS_BASE = {
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'zo-sentinel/1.0 (mcp-trust-intelligence)'
}
SORT_OPTIONS = ['updated', 'stars', 'forks']
SCHEMA_VERIFIED = False
COLUMNS_CACHE = None


def log(msg):
    ts = datetime.datetime.utcnow().isoformat()
    print(f'[{ts}] {msg}', flush=True)


def check_single_instance():
    lock_file = LOCK_FILE
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists():
        pid = lock_file.read_text().strip()
        if pid.isdigit():
            try:
                os.kill(int(pid), 0)
                return False
            except OSError:
                pass
        lock_file.unlink()
    lock_file.write_text(str(os.getpid()))
    return True


def send_heartbeat():
    try:
        requests.post(
            WRITE_SERVICE_URL,
            json={'table': 'service_health', 'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.datetime.utcnow().isoformat()}, 'wait': True},
            timeout=5
        )
    except Exception as e:
        log(f'heartbeat error: {e}')


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def get_github_headers():
    headers = dict(HEADERS_BASE)
    token = os.environ.get('GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def is_authenticated():
    token = os.environ.get('GITHUB_TOKEN', '')
    return bool(token)


def load_cursor():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return data
        except Exception:
            pass
    return {'page': 1, 'total_count': 0, 'updated_at': datetime.datetime.utcnow().isoformat(), 'sort_index': 0}


def save_cursor(cursor):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(cursor))


def get_columns():
    global COLUMNS_CACHE, SCHEMA_VERIFIED
    if COLUMNS_CACHE is not None:
        return COLUMNS_CACHE
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': "SELECT column_name FROM information_schema.columns WHERE table_name='mcp_discovery_candidates'"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            COLUMNS_CACHE = [row['column_name'] for row in data.get('rows', [])]
            SCHEMA_VERIFIED = True
            return COLUMNS_CACHE
    except Exception as e:
        log(f'schema query error: {e}')
    return None


def verify_schema():
    cols = get_columns()
    if cols is None:
        log('WARNING: could not verify mcp_discovery_candidates schema')
        return False
    required = {'candidate_name', 'candidate_url', 'candidate_description', 'discovered_in_directory', 'discovered_status', 'last_seen', 'promoted'}
    missing = required - set(cols)
    if missing:
        log(f'WARNING: missing columns: {missing}')
        return False
    log(f'schema verified: {cols}')
    return True


def parse_rate_limit(response):
    remaining = int(response.headers.get('X-RateLimit-Remaining', 999))
    reset_epoch = int(response.headers.get('X-RateLimit-Reset', 0))
    return remaining, reset_epoch


def handle_rate_limit(remaining, reset_epoch):
    if remaining < 5:
        sleep_until = max(reset_epoch + 30 - time.time(), 0)
        log(f'rate limit low ({remaining}), sleeping {sleep_until:.0f}s until reset')
        time.sleep(sleep_until)
        return True
    return False


def fetch_page(page, sort_option):
    params = {
        'q': 'topic:mcp-server OR topic:model-context-protocol OR topic:modelcontextprotocol',
        'per_page': PER_PAGE,
        'page': page,
        'sort': sort_option,
        'order': 'desc'
    }
    headers = get_github_headers()
    url = BASE_URL
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 403:
            try:
                body = resp.json()
                if 'rate limit' in str(body).lower():
                    log('rate limited (403), skipping cycle')
                    return None, 0, 0, True
            except Exception:
                pass
            log(f'403 error: {resp.text[:200]}')
            return None, 0, 0, True
        if resp.status_code != 200:
            log(f'HTTP {resp.status_code}: {resp.text[:200]}')
            return None, 0, 0, False
        data = resp.json()
        remaining, reset_epoch = parse_rate_limit(resp)
        total_count = data.get('total_count', 0)
        items = data.get('items', [])
        return items, total_count, remaining, reset_epoch
    except Exception as e:
        log(f'fetch error: {e}')
        return None, 0, 0, False


def write_repos(items):
    written = 0
    errors = 0
    now = datetime.datetime.utcnow().isoformat()
    for item in items:
        full_name = item.get('full_name', '')
        html_url = item.get('html_url', '')
        description = (item.get('description') or '')[:500]
        row = {
            'candidate_name': full_name,
            'candidate_url': html_url,
            'candidate_description': description,
            'discovered_in_directory': 'github_topic',
            'discovered_status': 'active',
            'last_seen': now,
            'promoted': False
        }
        try:
            resp = requests.post(
                WRITE_SERVICE_URL,
                json={'table': 'mcp_discovery_candidates', 'rows': row, 'wait': True},
                timeout=10
            )
            if resp.status_code in (200, 201):
                written += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            log(f'write error for {full_name}: {e}')
    return written, errors


def cycle():
    global SCHEMA_VERIFIED
    if not SCHEMA_VERIFIED:
        verify_schema()

    cursor = load_cursor()
    page = cursor.get('page', 1)
    total_count = cursor.get('total_count', 0)
    sort_index = cursor.get('sort_index', 0)
    seen = cursor.get('seen', 0)

    sort_option = SORT_OPTIONS[sort_index % len(SORT_OPTIONS)]

    log(f'fetching page {page} sort={sort_option}')
    items, fetched_total, rl_remaining, rl_reset = fetch_page(page, sort_option)

    if items is None:
        log(f'fetch failed or rate limited, skipping cycle')
        return

    if handle_rate_limit(rl_remaining, rl_reset):
        pass

    written, errors = write_repos(items)

    if total_count == 0 and fetched_total > 0:
        total_count = fetched_total
        log(f'total_count updated to {total_count}')

    seen += len(items)
    is_exhausted = (total_count > 0 and seen >= min(total_count, 1000)) or page >= MAX_PAGES

    if is_exhausted:
        log(f'exhausted at seen={seen}, total={total_count}, resetting')
        page = 1
        seen = 0
        sort_index = (sort_index + 1) % len(SORT_OPTIONS)
        sort_option = SORT_OPTIONS[sort_index]
        log(f'rotating sort to {sort_option}')
    else:
        page = page + 1

    cursor['page'] = page
    cursor['total_count'] = total_count
    cursor['sort_index'] = sort_index
    cursor['seen'] = seen
    cursor['updated_at'] = datetime.datetime.utcnow().isoformat()
    save_cursor(cursor)

    auth_str = 'auth' if is_authenticated() else 'unauth'
    sleep_time = 1 if is_authenticated() else 6
    log(f'cycle done page={page} total={total_count} seen={seen} written={written} rl_remaining={rl_remaining} errors={errors} auth={auth_str} sleeping={sleep_time}s')
    time.sleep(sleep_time)


def run():
    if not check_single_instance():
        log('already running, exiting')
        return

    log(f'{SERVICE_NAME} starting')

    try:
        os.makedirs('/home/workspace/logs', exist_ok=True)
    except Exception:
        pass

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    while True:
        try:
            cycle()
        except Exception as e:
            log(f'cycle error: {e}')
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    run()