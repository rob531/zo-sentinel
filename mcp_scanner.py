#!/usr/bin/env python3
"""
mcp_scanner.py -- ZO-SENTINEL MCP server registry scanner.
Sources: npm @modelcontextprotocol scope, Smithery marketplace, GitHub topic:mcp-server
"""
import hashlib, json, logging, os, signal, sys, time
from datetime import datetime, timezone
import requests

SERVICE_NAME    = 'mcp_scanner'
WRITE_SERVICE   = 'http://127.0.0.1:8772'   # base URL only -- no trailing path
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL   = 21600  # 6 hours

log = logging.getLogger(__name__)

# ── mcp_traffic_fingerprints import ──────────────────────────────────────────
# Wire protocol confirmation into scanner discovery
sys.path.insert(0, '/home/workspace')
try:
    from mcp_traffic_fingerprints import (
        detect_mcp_methods,
        is_mcp_traffic,
        extract_session_indicators,
        MCP_METHODS as FINGERPRINT_METHODS,
    )
    FINGERPRINT_LIB_LOADED = True
except ImportError:
    FINGERPRINT_LIB_LOADED = False
    log.warning('mcp_traffic_fingerprints not available - protocol confirmation disabled')


def check_single_instance():
    pid_file = f'/tmp/{SERVICE_NAME}.pid'
    if os.path.exists(pid_file):
        try:
            old_pid = int(open(pid_file).read().strip())
            os.kill(old_pid, 0)
            log.warning('Already running with PID %d', old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    open(pid_file, 'w').write(str(os.getpid()))
    def cleanup(sig, frame):
        try: os.remove(pid_file)
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def ws_write(table, row):
    """Write to DuckDB via write_service."""
    r = requests.post(WRITE_SERVICE + '/write',
        json={'table': table, 'rows': row, 'wait': True}, timeout=10)
    r.raise_for_status()
    return r.status_code == 200


def ws_query(sql):
    """Query DuckDB via write_service (port 8772, not 8773)."""
    r = requests.post(WRITE_SERVICE + '/query',
        json={'sql': sql}, timeout=10)
    if r.status_code == 200:
        return r.json().get('rows', [])
    return []


def heartbeat():
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)


def server_id(url):
    return hashlib.md5(url.encode()).hexdigest()


def server_exists(sid):
    rows = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE server_id='{sid}' LIMIT 1")
    return bool(rows)


def confirm_mcp_protocol(url: str, timeout: int = 10) -> dict:
    """
    Confirm MCP protocol by probing the candidate server URL.
    Uses mcp_traffic_fingerprints to detect MCP JSON-RPC signatures.
    
    Returns dict:
      - confirmed: bool
      - methods: list of detected MCP methods
      - confidence: float (0.0-1.0)
      - headers: dict of MCP session indicators
    """
    if not FINGERPRINT_LIB_LOADED:
        return {"confirmed": False, "methods": [], "confidence": 0.0, "headers": {}}
    
    result = {
        "confirmed": False,
        "methods": [],
        "confidence": 0.0,
        "headers": {},
    }
    
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "Accept": "application/json, */*",
                "User-Agent": "MCP-Scanner/1.0 (zo-sentinel)"
            },
            allow_redirects=True
        )
        
        if resp.status_code != 200:
            return result
        
        body = resp.text
        resp_headers = dict(resp.headers)
        
        # Use fingerprint library to detect MCP protocol
        if is_mcp_traffic(body):
            result["confirmed"] = True
            result["methods"] = detect_mcp_methods(body)
            result["headers"] = extract_session_indicators(resp_headers)
            
            # Confidence based on methods detected
            method_count = len(result["methods"])
            result["confidence"] = min(1.0, 0.3 + (method_count * 0.15))
            
            log.info('Protocol confirmed for %s: methods=%s confidence=%.2f',
                     url, result["methods"], result["confidence"])
        else:
            log.debug('No MCP protocol in response from %s', url)
            
    except requests.exceptions.Timeout:
        log.debug('Timeout probing %s for protocol confirmation', url)
    except requests.exceptions.ConnectionError:
        log.debug('Connection error probing %s', url)
    except Exception as e:
        log.warning('Protocol confirmation error for %s: %s', url, e)
    
    return result


def upsert(name, url, description, source, metadata=None):
    sid = server_id(url)
    if server_exists(sid):
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        ws_write('mcp_server_registry', {
            'server_id': sid, 'name': name, 'url': url,
            'description': description or '',
            'registry_source': source,
            'scan_count': 1,
            'first_seen': now, 'last_scanned': now,
            'metadata': json.dumps(metadata or {})
        })
        return True
    except Exception as e:
        log.warning('upsert failed for %s: %s', name, e)
        return False


# ---------------------------------------------------------------------------
# Source 1: npm @modelcontextprotocol scope
# Correct API: search by package name prefix, not scope: syntax
# ---------------------------------------------------------------------------

def scan_npm():
    stored = skipped = errors = 0

    # Query 1: official @modelcontextprotocol org packages
    searches = [
        '@modelcontextprotocol',
        'modelcontextprotocol mcp server',
        'mcp-server',
    ]

    seen_names = set()
    for query in searches:
        try:
            r = requests.get(
                'https://registry.npmjs.org/-/v1/search',
                params={'text': query, 'size': 250},
                timeout=15
            )
            r.raise_for_status()
            for obj in r.json().get('objects', []):
                pkg = obj.get('package', {})
                name = pkg.get('name', '')
                if not name or name in seen_names: continue
                seen_names.add(name)
                # Only include packages that look like MCP servers
                if not any(kw in name.lower() for kw in
                           ['mcp', 'modelcontext', 'model-context']):
                    skipped += 1
                    continue
                npm_url = (pkg.get('links', {}).get('npm') or
                           f'https://www.npmjs.com/package/{name}')
                
                # ── Protocol fingerprint validation ───────────────────────────
                protocol_confirmed = False
                if FINGERPRINT_LIB_LOADED and pkg.get('description'):
                    # Validate description contains MCP method indicators
                    description = pkg.get('description', '')
                    protocol_confirmed = is_mcp_traffic(description)
                
                ok = upsert(
                    name=name,
                    url=npm_url,
                    description=pkg.get('description', ''),
                    source='npm_official',
                    metadata={
                        'version': pkg.get('version', ''),
                        'date': pkg.get('date', ''),
                        'publisher': pkg.get('publisher', {}).get('username', ''),
                        'protocol_confirmed': protocol_confirmed,
                    }
                )
                if ok: stored += 1
                else: skipped += 1
        except Exception as e:
            log.error('npm search "%s" error: %s', query, e)
            errors += 1
        time.sleep(0.5)  # npm rate limit

    log.info('npm scan: %d stored, %d skipped, %d errors', stored, skipped, errors)
    return stored


# ---------------------------------------------------------------------------
# Source 2: GitHub topic:mcp-server
# ---------------------------------------------------------------------------

def scan_github():
    stored = skipped = 0
    headers = {}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'
        log.info('GitHub: using authenticated requests')
    else:
        log.warning('GitHub: no GITHUB_TOKEN -- rate limit 10 req/min')

    topics = ['mcp-server', 'model-context-protocol', 'mcp-tool']
    seen_urls = set()

    for topic in topics:
        try:
            r = requests.get(
                'https://api.github.com/search/repositories',
                params={'q': f'topic:{topic}', 'sort': 'stars',
                        'order': 'desc', 'per_page': 50},
                headers=headers, timeout=15
            )
            if r.status_code == 403:
                log.warning('GitHub rate limited -- skipping remaining topics')
                break
            r.raise_for_status()
            for item in r.json().get('items', []):
                url = item.get('html_url', '')
                if not url or url in seen_urls: continue
                seen_urls.add(url)
                ok = upsert(
                    name=item.get('name', ''),
                    url=url,
                    description=(item.get('description') or '')[:500],
                    source='github',
                    metadata={'stars': item.get('stargazers_count', 0),
                               'pushed_at': item.get('pushed_at', ''),
                               'language': item.get('language', ''),
                               'full_name': item.get('full_name', '')}
                )
                if ok: stored += 1
                else: skipped += 1
        except Exception as e:
            log.error('GitHub topic:%s error: %s', topic, e)
        time.sleep(1)  # GitHub rate limit

    log.info('GitHub scan: %d stored, %d skipped', stored, skipped)
    return stored


# ---------------------------------------------------------------------------
# Source 3: Smithery MCP marketplace
# ---------------------------------------------------------------------------

def scan_smithery():
    stored = skipped = 0
    try:
        r = requests.get(
            'https://registry.smithery.ai/servers',
            params={'pageSize': 100, 'page': 1},
            timeout=15,
            headers={'Accept': 'application/json'}
        )
        if r.status_code == 200:
            data = r.json()
            servers = data.get('servers', data.get('items', []))
            for s in servers:
                name = s.get('qualifiedName') or s.get('name', '')
                url = s.get('url') or f'https://smithery.ai/server/{name}'
                ok = upsert(
                    name=name,
                    url=url,
                    description=(s.get('description') or '')[:500],
                    source='smithery',
                    metadata={'verified': s.get('isVerified', False),
                               'tools': len(s.get('tools', [])),
                               'homepage': s.get('homepage', '')}
                )
                if ok: stored += 1
                else: skipped += 1
        else:
            log.warning('Smithery returned HTTP %d', r.status_code)
    except Exception as e:
        log.warning('Smithery scan error: %s', e)
    log.info('Smithery scan: %d stored, %d skipped', stored, skipped)
    return stored


# ---------------------------------------------------------------------------
# Source 4: Known high-value MCP servers (static seed)
# Ensures registry always has meaningful data even if APIs change
# ---------------------------------------------------------------------------

KNOWN_MCP_SERVERS = [
    {'name': '@modelcontextprotocol/server-filesystem',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem',
     'description': 'Official MCP server providing filesystem access tools',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-github',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-github',
     'description': 'Official MCP server for GitHub API integration',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-postgres',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-postgres',
     'description': 'Official MCP server for PostgreSQL database access',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-slack',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-slack',
     'description': 'Official MCP server for Slack workspace integration',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-brave-search',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-brave-search',
     'description': 'Official MCP server for Brave Search API',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-google-maps',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-google-maps',
     'description': 'Official MCP server for Google Maps integration',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-memory',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-memory',
     'description': 'Official MCP server for persistent memory and knowledge graphs',
     'source': 'npm_official'},
    {'name': '@modelcontextprotocol/server-puppeteer',
     'url': 'https://www.npmjs.com/package/@modelcontextprotocol/server-puppeteer',
     'description': 'Official MCP server for browser automation via Puppeteer',
     'source': 'npm_official'},
    {'name': 'mcp-server-kubernetes',
     'url': 'https://github.com/Flux159/mcp-server-kubernetes',
     'description': 'MCP server for Kubernetes cluster management',
     'source': 'github'},
    {'name': 'mcp-server-docker',
     'url': 'https://github.com/ckreiling/mcp-server-docker',
     'description': 'MCP server for Docker container management',
     'source': 'github'},
]

def seed_known_servers():
    stored = sum(
        1 for s in KNOWN_MCP_SERVERS
        if upsert(s['name'], s['url'], s['description'], s['source'])
    )
    log.info('Known servers seed: %d new entries', stored)
    return stored


# ---------------------------------------------------------------------------
# Cycle + daemon
# ---------------------------------------------------------------------------

def cycle():
    log.info('Starting MCP registry scan cycle')
    heartbeat()
    total = 0
    total += seed_known_servers()   # always first -- guarantees baseline data
    total += scan_npm()
    total += scan_github()
    total += scan_smithery()
    # Check what we now have
    rows = ws_query('SELECT COUNT(*) as n FROM mcp_server_registry')
    count = rows[0]['n'] if rows else '?'
    log.info('Scan cycle complete: %d new servers added. Registry total: %s', total, count)
    return total


def run():
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    check_single_instance()
    log.info('Starting %s daemon', SERVICE_NAME)
    heartbeat()
    while True:
        try:
            cycle()
        except Exception as e:
            log.error('Cycle error: %s', e)
        heartbeat()
        log.info('Sleeping for %ds until next scan', POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    run()