import sys
sys.path.insert(0, '/home/workspace')
import os
import time
import shutil
import subprocess
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
# GitHub search returns at most 1000 results (10 pages x 100) per query, and
# answers 422 beyond that regardless of authentication. Naming it stops the
# sweep from requesting 490 pages that cannot exist.
GITHUB_SEARCH_MAX_PAGES = 10
# The write service caps an UNBOUNDED select at 200 rows and flags nothing.
# Every read of the registry must carry an explicit LIMIT above its size.
QUERY_ROW_LIMIT = 100000

# --- GitHub authentication -------------------------------------------------
# Verified live against x-ratelimit-limit (not from docs), 2026-08-01:
# the GitHub *search* API allows 10 req/min unauthenticated, 30 req/min
# authenticated. This daemon has run anonymous since 2026-07-11 and burned its
# whole minute budget by page ~10, logging ~200 rate-limit 403s/day.
#
# IMPORTANT -- what this does NOT fix. Pages 11+ return HTTP 422 even when fully
# authenticated with 29/30 budget remaining: GitHub caps ANY search query at
# 1000 results (10 pages x 100). So MAX_PAGES = 500 is unreachable here by
# construction, and authentication does NOT deepen the corpus. What it does fix
# is that pages 1-10 now complete without exhausting the budget, and the sweep
# then terminates cleanly on the 422 instead of thrashing on 403s. Lifting the
# real intake ceiling requires QUERY DIVERSIFICATION (the 1000 cap is per
# query), which is a separate change.
#
# This resolves an ALREADY-PROVISIONED credential; it does not create or store
# one. Resolution order, first hit wins, and it FAILS SOFT to anonymous so
# behaviour is never worse than before. The token value is never logged.
_GH_TOKEN_CACHE = None
_GH_TOKEN_RESOLVED = False


def github_token():
    """Resolve an existing GitHub token, or None. Never raises, never logs the value."""
    global _GH_TOKEN_CACHE, _GH_TOKEN_RESOLVED
    if _GH_TOKEN_RESOLVED:
        return _GH_TOKEN_CACHE
    _GH_TOKEN_RESOLVED = True

    for var in ('GITHUB_TOKEN', 'GH_TOKEN'):
        tok = os.environ.get(var)
        if tok:
            _GH_TOKEN_CACHE = tok.strip()
            return _GH_TOKEN_CACHE

    # the gh CLI credential already present on the tower
    if shutil.which('gh'):
        try:
            out = subprocess.run(['gh', 'auth', 'token'], capture_output=True,
                                 text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                _GH_TOKEN_CACHE = out.stdout.strip()
                return _GH_TOKEN_CACHE
        except Exception:
            pass

    # AgentVault convention -- tower-side only; absent on the Linux box, so this
    # is attempted last and its absence is not an error.
    fetch_secret = os.environ.get('AGENTVAULT_FETCH_SECRET', r'D:\agentvault\fetch_secret.py')
    if os.path.exists(fetch_secret):
        try:
            out = subprocess.run([sys.executable, fetch_secret, 'github'],
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                _GH_TOKEN_CACHE = out.stdout.strip()
                return _GH_TOKEN_CACHE
        except Exception:
            pass

    return None


def github_headers():
    """Auth headers when a token is available, otherwise the anonymous path."""
    headers = {'Accept': 'application/vnd.github+json'}
    tok = github_token()
    if tok:
        headers['Authorization'] = 'Bearer ' + tok
    return headers

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

def get_promoted_keys():
    """Existing (server_id, url) keys. Returns (server_ids, urls, trustworthy).

    The write service silently caps an unbounded SELECT at 200 rows and sets no
    truncation flag -- it just returns `count: 200`. This function previously ran
    `SELECT url FROM mcp_server_registry` with no LIMIT and therefore compared
    every candidate against **198 distinct urls out of 2912**, so ~949 already-known
    servers looked NEW on every single cycle and were re-written forever.

    `server_id` is the real uniqueness key (2912 distinct / 2912 rows); `url` is
    NOT (2501 distinct / 2912 rows). Filtering on url alone can never agree with
    the constraint the database actually enforces.

    `trustworthy` is False when we cannot prove we read the whole table. An
    under-read must NOT be treated as "these rows do not exist" -- unknown is not
    zero, and that confusion is the entire defect being fixed here.
    """
    expected = get_reg_count()
    try:
        r = requests.post(QUERY_URL, json={
            'sql': 'SELECT server_id, url FROM mcp_server_registry LIMIT %d' % QUERY_ROW_LIMIT
        }, timeout=60)
        rows = r.json().get('rows', [])
    except Exception as e:
        log(f'Failed to fetch existing keys: {e}')
        return set(), set(), False

    server_ids = {row['server_id'] for row in rows if row.get('server_id')}
    urls = {row['url'] for row in rows if row.get('url')}

    trustworthy = True
    if expected is not None and expected >= 0 and len(rows) < expected:
        log(f'!! EXISTENCE FILTER TRUNCATED: read {len(rows)} of {expected} registry rows '
            f'(QUERY_ROW_LIMIT={QUERY_ROW_LIMIT}). Treating candidates as UNKNOWN, not new.')
        trustworthy = False
    if len(rows) >= QUERY_ROW_LIMIT:
        log(f'!! EXISTENCE FILTER AT LIMIT: {len(rows)} rows == QUERY_ROW_LIMIT; raise it.')
        trustworthy = False
    return server_ids, urls, trustworthy

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
    if count_before < 0 or count_after < 0:
        # get_reg_count() returns -1 on failure. Unknown is not zero and is not
        # a success either -- say so rather than reporting a made-up delta.
        log(f'INSERT batch: before={count_before}, inserted_attempted={len(servers)}, '
            f'after={count_after}, actual_new=UNKNOWN (registry count unavailable)')
        return None
    actual_new = count_after - count_before
    log(f'INSERT batch: before={count_before}, inserted_attempted={len(servers)}, '
        f'after={count_after}, actual_new={actual_new}')
    if actual_new == 0 and len(servers) > 0:
        log(f'!! WRITE PATH NO-OP: attempted {len(servers)} rows, registry did not move. '
            f'Every one was already present, or the write silently failed.')
    # Return what ACTUALLY landed. Returning the attempt is what let this daemon
    # claim 2,516,942 promotions against 469 real inserts since 2026-07-11.
    return actual_new

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
    headers = github_headers()
    log('GitHub auth: %s' % ('token' if 'Authorization' in headers else 'ANONYMOUS (10 req/min ceiling)'))
    for page in range(1, min(MAX_PAGES, GITHUB_SEARCH_MAX_PAGES) + 1):
        page_count += 1
        params = {'q': 'mcp server MCP-client MCP-server in:name,description', 'sort': 'stars', 'per_page': 100, 'page': page}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            remaining = resp.headers.get('x-ratelimit-remaining')
            if remaining is not None and remaining.isdigit() and int(remaining) <= 1:
                log('GitHub rate budget nearly spent: remaining=%s limit=%s (page %s)'
                    % (remaining, resp.headers.get('x-ratelimit-limit'), page))
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
    existing_ids, existing_urls, filter_ok = get_promoted_keys()
    before_filter = len(candidates)
    if filter_ok:
        # Filter on server_id FIRST -- it is the key the database actually
        # enforces -- then on url as a secondary guard.
        candidates = [c for c in candidates
                      if c.get('url')
                      and c.get('server_id') not in existing_ids
                      and c['url'] not in existing_urls]
        log(f'Candidates: {before_filter} total, '
            f'{before_filter - len(candidates)} skipped (existing), {len(candidates)} new '
            f'(filter read {len(existing_ids)} server_ids)')
    else:
        # We could not prove we read the whole registry. Writing anyway is
        # harmless (the constraint dedups) but claiming these are NEW is not.
        log(f'Candidates: {before_filter} total, existence filter UNTRUSTWORTHY -- '
            f'new-count SUPPRESSED this cycle; relying on the DB constraint to dedup.')
    promoted = upsert_servers(candidates)
    total_promoted = None if (promoted is None or total_promoted is None) else total_promoted + promoted
    reg_count_after = get_reg_count()
    delta = reg_count_after - reg_count_before
    if total_promoted is None:
        log(f'Cycle complete: promoted=UNKNOWN (registry count unavailable), '
            f'registry now {reg_count_after}')
    else:
        # total_promoted is now what LANDED, not what was attempted, so it and
        # `delta` agree by construction. If they ever diverge, something else
        # wrote to the registry during the cycle -- worth seeing.
        log(f'Cycle complete: {total_promoted} promoted, registry now {reg_count_after} (delta: {delta:+d})')
        if total_promoted != delta:
            log(f'!! ACCOUNTING MISMATCH: promoted={total_promoted} but registry delta={delta} '
                f'-- a concurrent writer, or the count is unreliable.')
    elapsed = time.time() - start
    log(f'Cycle took {elapsed:.1f}s')
    send_heartbeat()

if __name__ == '__main__':
    run()
    while True:
        time.sleep(POLL_SECS)
        send_heartbeat()
        run()