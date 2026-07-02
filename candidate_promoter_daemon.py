import sys
sys.path.insert(0, '/home/workspace')
import time
import requests
from datetime import datetime

PID_FILE = '/tmp/candidate_promoter_daemon.pid'
SERVICE_NAME = 'candidate_promoter_daemon'
POLL_SECS = 300
WRITE_URL = 'http://127.0.0.1:8772/write'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
MAX_PAGES = 500
BATCH_SIZE = 100

def check_single_instance():
    import os
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = f.read().strip()
        if existing and existing != pid:
            try:
                os.kill(int(existing), 0)
                print(f'Instance already running with PID {existing}')
                sys.exit(0)
            except OSError:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)

def send_heartbeat():
    try:
        requests.post(WRITE_URL, json={
            'table': 'service_health',
            'rows': {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.utcnow().isoformat()
            },
            'wait': True
        }, timeout=10)
    except Exception as e:
        print(f'Heartbeat failed: {e}')

def log(msg):
    ts = datetime.utcnow().isoformat()
    print(f'[{ts}] {msg}')

def fetch_page(url, page=1, per_page=100):
    params = {'page': page, 'per_page': per_page}
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        items = data.get('results', data.get('packages', []))
        next_cursor = data.get('next')
        if next_cursor is None:
            if isinstance(data.get('links', {}).get('next'), dict):
                next_cursor = data['links']['next'].get('url')
        return items, next_cursor
    except Exception as e:
        log(f'Page fetch error at page {page}: {e}')
        return [], None

def get_reg_count():
    try:
        r = requests.post(QUERY_URL, json={'sql': 'SELECT COUNT(*) as cnt FROM mcp_server_registry'}, timeout=15)
        result = r.json()
        return result.get('rows', [{}])[0].get('cnt', 0)
    except:
        return -1

def get_promoted_urls():
    try:
        r = requests.post(QUERY_URL, json={'sql': 'SELECT url FROM mcp_server_registry'}, timeout=30)
        result = r.json()
        return {row['url'] for row in result.get('rows', []) if row.get('url')}
    except Exception as e:
        log(f'Failed to fetch existing URLs: {e}')
        return set()

def upsert_servers(servers):
    if not servers:
        return 0
    count_before = get_reg_count()
    inserted = 0
    for i in range(0, len(servers), BATCH_SIZE):
        batch = servers[i:i+BATCH_SIZE]
        rows = []
        for s in batch:
            rows.append({
                'server_id': s.get('server_id') or (s.get('name') or '').lower().replace(' ', '-'),
                'name': s.get('name') or '',
                'url': s.get('url') or s.get('homepage') or s.get('repository') or '',
                'description': (s.get('description') or '')[:500],
                'trust_score': 5,
                'verdict': 'unreviewed',
                'registry_source': 'candidate_promoter',
                'scan_count': 0
            })
        try:
            requests.post(WRITE_URL, json={
                'table': 'mcp_server_registry',
                'rows': rows,
                'wait': True
            }, timeout=30)
            inserted += len(batch)
        except Exception as e:
            log(f'Batch write error: {e}')
    count_after = get_reg_count()
    actual_new = count_after - count_before
    log(f'INSERT batch: before={count_before}, inserted_attempted={len(servers)}, after={count_after}, actual_new={actual_new}')
    return inserted

def fetch_candidates_from_npm():
    candidates = []
    page_count = 0
    cursor = None
    url = 'https://registry.npmjs.org/-/v1/search'
    while page_count < MAX_PAGES:
        page_count += 1
        params = {'text': 'mcp', 'size': 100, 'from': page_count * 100 if not cursor else 0}
        if cursor:
            params['from'] = 0
            params['query'] = f'mcp cursor:{cursor}'
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
            items = data.get('objects', [])
            if not items:
                break
            for item in items:
                pkg = item.get('package', {})
                candidates.append({
                    'name': pkg.get('name', ''),
                    'url': pkg.get('links', {}).get('npm') or pkg.get('homepage', ''),
                    'description': pkg.get('description', ''),
                    'server_id': pkg.get('name', '').lower().replace(' ', '-')
                })
            next_cursor = data.get('nextCursor')
            if not next_cursor:
                break
            cursor = next_cursor
            log(f'npm page {page_count} fetched, next_cursor={str(cursor)[:30]}')
        except Exception as e:
            log(f'npm fetch error page {page_count}: {e}')
            break
    return candidates, page_count

def fetch_candidates_from_github():
    candidates = []
    page_count = 0
    url = 'https://api.github.com/search/repositories'
    for page in range(1, MAX_PAGES + 1):
        page_count += 1
        params = {'q': 'mcp server MCP-client MCP-server in:name,description', 'sort': 'stars', 'per_page': 100, 'page': page}
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 404 or resp.status_code == 422:
                break
            resp.raise_for_status()
            data = resp.json()
            items = data.get('items', [])
            if not items:
                break
            for item in items:
                candidates.append({
                    'name': item.get('full_name', ''),
                    'url': item.get('html_url', ''),
                    'description': item.get('description', ''),
                    'server_id': item.get('full_name', '').lower().replace(' ', '-')
                })
            log(f'GitHub page {page_count} fetched ({len(items)} items)')
            if page_count >= MAX_PAGES:
                break
            if not data.get('incomplete_results', False) and len(items) < 100:
                break
        except Exception as e:
            log(f'GitHub fetch error page {page_count}: {e}')
            break
    return candidates, page_count

def run():
    check_single_instance()
    start = time.time()
    log(f'Starting {SERVICE_NAME}')
    send_heartbeat()
    reg_count_before = get_reg_count()
    log(f'Registry count at cycle start: {reg_count_before}')
    total_promoted = 0
    all_candidates = []
    all_sources = [
        ('npmjs', fetch_candidates_from_npm),
        ('github', fetch_candidates_from_github),
    ]
    for source_name, fetch_fn in all_sources:
        candidates, page_count = fetch_fn()
        log(f'{source_name}: {len(candidates)} candidates from {page_count} pages')
        all_candidates.extend(candidates)
    dedup = {}
    for c in all_candidates:
        key = c.get('url') or c.get('name', '')
        if key and key not in dedup:
            dedup[key] = c
    candidates = list(dedup.values())
    existing_urls = get_promoted_urls()
    before_filter = len(candidates)
    candidates = [c for c in candidates if c.get('url') and c['url'] not in existing_urls]
    log(f'Candidates: {before_filter} total, {before_filter - len(candidates)} skipped (existing), {len(candidates)} new')
    promoted = upsert_servers(candidates)
    total_promoted += promoted
    reg_count_after = get_reg_count()
    log(f'Cycle complete: {total_promoted} promoted, registry now {reg_count_after} (delta: +{reg_count_after - reg_count_before})')
    elapsed = time.time() - start
    log(f'Cycle took {elapsed:.1f}s')
    send_heartbeat()

if __name__ == '__main__':
    run()
    while True:
        time.sleep(POLL_SECS)
        send_heartbeat()
        run()