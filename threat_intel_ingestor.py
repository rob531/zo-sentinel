import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os
import time
import requests
import hashlib
import re
import traceback
from datetime import datetime, timedelta

SERVICE_NAME = 'threat_intel_ingestor'
SERVICE_PORT = 8788
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 3600
LOG_FILE = f'/home/workspace/zo_sentinel/logs/{SERVICE_NAME}.log'

OSV_API_BASE = 'https://api.osv.dev/v1'
OSV_ECOSYSTEMS = ['npm', 'PyPI', 'Go', 'NuGet', 'Maven', 'RubyGems', 'Pub', 'CocoaPods']
SEVERITY_KEYWORDS = {
    'critical': ['rce', 'remote code execution', 'privilege escalation', '0day', 'zero-day', 'unauthenticated', 'critical', 'severe', 'data breach', 'supply chain attack'],
    'high': ['sql injection', 'xss', 'cross-site scripting', 'path traversal', 'authentication bypass', 'deserialization', 'xxe', 'ssrf', 'command injection', 'buffer overflow'],
    'medium': ['csrf', 'clickjacking', 'information disclosure', 'denial of service', 'open redirect', 'dos', 'bypass', 'weak cryptographic'],
    'low': ['hardcoded', 'insecure', 'deprecated', 'cve', 'security advisory'],
}
THREAT_KEYWORDS = ['mcp', 'model context protocol', 'server', 'package', 'dependency', 'supply chain', 'npm', 'python', 'repository']
BLACKLIST_DOMAINS = ['example.com', 'test.com', 'localhost']

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def ws_write(table, rows):
    """Write rows via write_service. Returns the response dict, or None on
    TOTAL failure after 3 attempts.

    None means NOTHING WAS PERSISTED. Callers must check it. Before FU-190 no
    caller did, so `Recorded OSV vuln ...` was logged 18,560 times against
    55,690 HTTP 500s and zero rows in the table -- the daemon's own success
    message was the reason nobody noticed it had never written anything.
    """
    payload = {'table': table, 'rows': rows, 'wait': True}
    last = ''
    for attempt in range(3):
        try:
            r = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return r.json()
            last = f'HTTP {r.status_code} {r.text[:200]}'
            log(f'ws_write warning ({table}): {last}')
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
            log(f'ws_write attempt {attempt+1} error ({table}): {last}')
        time.sleep(2)
    log(f'ws_write FAILED after 3 attempts -- NOTHING PERSISTED to {table}: {last}')
    return None

def ws_query(sql):
    payload = {'sql': sql}
    for attempt in range(3):
        try:
            r = requests.post(QUERY_URL, json=payload, timeout=60)
            if r.status_code == 200:
                return r.json()
            log(f'ws_query warning: {r.status_code} {r.text[:200]}')
        except Exception as e:
            log(f'ws_query attempt {attempt+1} error: {e}')
        time.sleep(2)
    return None

def ws_execute(sql):
    payload = {'sql': sql}
    for attempt in range(3):
        try:
            r = requests.post(EXECUTE_URL, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return r.json()
            log(f'ws_execute warning: {r.status_code} {r.text[:200]}')
        except Exception as e:
            log(f'ws_execute attempt {attempt+1} error: {e}')
        time.sleep(2)
    return None

def send_heartbeat():
    ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': datetime.utcnow().isoformat()})

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read().strip())
        if old_pid != pid:
            try:
                os.kill(old_pid, 0)
                log(f'Instance already running with PID {old_pid}, exiting')
                sys.exit(1)
            except OSError:
                log(f'Stale PID file, overwriting')
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    log(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)

def threat_already_recorded(server_id, threat_type, article_url):
    result = ws_query(f"SELECT 1 FROM mcp_threat_associations WHERE server_id = '{server_id}' AND threat_type = '{threat_type}' AND evidence LIKE '%{article_url[:50]}%' LIMIT 1")
    return result and result.get('rows') and len(result['rows']) > 0

def compute_threat_severity(text):
    text_lower = text.lower()
    if any(kw in text_lower for kw in SEVERITY_KEYWORDS['critical']):
        return 'critical'
    if any(kw in text_lower for kw in SEVERITY_KEYWORDS['high']):
        return 'high'
    if any(kw in text_lower for kw in SEVERITY_KEYWORDS['medium']):
        return 'medium'
    if any(kw in text_lower for kw in SEVERITY_KEYWORDS['low']):
        return 'low'
    return 'unknown'

def is_relevant_to_mcp(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in THREAT_KEYWORDS)

def sanitize_for_sql(text):
    if not text:
        return ''
    return text.replace("'", "''").replace('\\', '\\\\')[:2000]

def create_tables():
    # 2026-05-28 patch: align with full_schema_bootstrap.py's canonical
    # mcp_threat_associations schema. Two changes:
    #   1. Drop `evidence(100)` -- MySQL prefix-length syntax not supported
    #      by DuckDB. Was raising parser errors every cycle, which tripped
    #      DuckDB's heap-corruption bug and SIGABRT'd write_service (exit
    #      code 134 / malloc tcache).
    #   2. Use canonical column shape: id PRIMARY KEY (seq_threat_id),
    #      add `source` column with UNIQUE (server_id, threat_type, source).
    #      Both this CREATE and the canonical one are CREATE TABLE IF NOT
    #      EXISTS, so whichever runs first wins; aligning here so the two
    #      authors agree on schema if someone reads either file.
    ws_execute('''
        CREATE SEQUENCE IF NOT EXISTS seq_threat_id START 1
    ''')
    ws_execute('''
        CREATE TABLE IF NOT EXISTS mcp_threat_associations (
            id BIGINT PRIMARY KEY DEFAULT nextval('seq_threat_id'),
            server_id VARCHAR NOT NULL,
            threat_type VARCHAR NOT NULL,
            severity VARCHAR,
            evidence TEXT,
            source VARCHAR,
            reported_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (server_id, threat_type, source)
        )
    ''')
    ws_execute('''
        CREATE TABLE IF NOT EXISTS threat_intel_articles (
            id VARCHAR PRIMARY KEY,
            url VARCHAR,
            title VARCHAR,
            summary TEXT,
            topics VARCHAR,
            importance VARCHAR,
            source VARCHAR,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    log('Tables ensured')

def get_mcp_servers_for_osv_scan():
    result = ws_query('''
        SELECT server_id, name, url
        FROM mcp_server_registry
        ORDER BY trust_score ASC NULLS FIRST
        LIMIT 500
    ''')
    if result and result.get('rows'):
        return result['rows']
    return []

def get_npm_packages_from_facts():
    result = ws_query('''
        SELECT server_id, name, metadata
        FROM mcp_registry_facts
        WHERE metadata LIKE '%npm%' OR metadata LIKE '%registry_source:npm%'
        LIMIT 500
    ''')
    if result and result.get('rows'):
        return result['rows']
    return []

def extract_package_name(url):
    # A SQL NULL deserializes to None, and `server.get('url', '')` does NOT
    # substitute the default for a key that is PRESENT with value None -- so
    # None reaches this function and re.search() raises TypeError. Guard at the
    # boundary rather than relying on the caller's default. (FU-187)
    if not isinstance(url, str) or not url:
        return None
    patterns = [
        r'npmjs\.com/(?:package/)?(@?[^/]+)',
        r'github\.com/([^/]+)/([^/]+)',
        r'pypi\.org/project/([^/]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1) if pattern.startswith(r'npmjs') else m.group(0).split('/')[-1]
    return None

def cvss_base_score(raw):
    """Return a numeric CVSS base score from an OSV severity `score`, or None.

    OSV reports CVSS severity as a VECTOR STRING, not a number:
        {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}
    The previous expression was `float(score.split(':')[0] if ':' in score else score)`,
    which takes the first colon-delimited segment of exactly that shape and so
    evaluates `float('CVSS')` -- a ValueError on EVERY CVSS_V3 entry, 100% of the
    time. It could only ever have worked on a "7.5:..." shape that OSV does not
    emit. A vector carries no scalar base score without full CVSS arithmetic, so
    return None and let the caller fall back to keyword severity. (FU-189)
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    # "CVSS:3.1/AV:N/..." -- a vector. No scalar available.
    return None


def query_osv(ecosystem, package):
    try:
        payload = {'package': {'name': package, 'ecosystem': ecosystem}, 'version': ''}
        r = requests.post(f'{OSV_API_BASE}/query', json=payload, timeout=15)
        if r.status_code == 200:
            return r.json().get('vulns', [])
    except Exception as e:
        log(f'OSV query error for {ecosystem}/{package}: {e}')
    return []

def process_world_articles():
    log('Querying world_articles for MCP/security articles...')
    articles_result = ws_query('''
        SELECT id, url, title, summary, topics, importance, visited_at, source
        FROM world_articles
        WHERE (topics LIKE '%mcp%' OR topics LIKE '%security%' OR topics LIKE '%vulnerability%')
          AND visited_at > NOW() - INTERVAL '48 hours'
        ORDER BY visited_at DESC
        LIMIT 100
    ''')
    
    if not articles_result or not articles_result.get('rows'):
        log('No relevant articles found in world_articles')
        return 0
    
    articles = articles_result['rows']
    log(f'Found {len(articles)} relevant articles from world_articles')
    
    count = 0
    for article in articles:
        article_id = article.get('id', '')
        article_url = article.get('url', '')
        article_title = article.get('title', '')
        article_summary = article.get('summary', '')
        topics = article.get('topics', '')
        importance = article.get('importance', 'medium')
        source = article.get('source', 'world_articles')
        
        if not is_relevant_to_mcp(article_title + ' ' + article_summary):
            continue
        
        article_hash = hashlib.md5((article_url + article_title).encode()).hexdigest()
        
        severity = compute_threat_severity(article_title + ' ' + article_summary)
        
        evidence = f'Article: {sanitize_for_sql(article_title)} | Summary: {sanitize_for_sql(article_summary[:500])} | Topics: {sanitize_for_sql(topics)}'
        
        threat_type = 'article_intel'
        
        try:
            ws_write('threat_intel_articles', {
                'id': article_hash,
                'url': article_url,
                'title': article_title[:500],
                'summary': article_summary[:2000],
                'topics': topics,
                'importance': importance,
                'source': source,
            })
            count += 1
        except Exception as e:
            log(f'Failed to write article {article_id}: {e}')
    
    log(f'Processed {count} articles from world_articles')
    return count

def process_osv_vulns():
    log('Scanning servers for OSV vulnerabilities...')
    servers = get_mcp_servers_for_osv_scan()
    log(f'Got {len(servers)} servers for OSV scan (ordered by trust_score ASC)')
    
    if not servers:
        log('No servers found for OSV scan')
        return 0
    
    vuln_count = 0
    scanned = 0
    write_failures = 0
    
    for server in servers:
        server_id = server.get('server_id')
        name = server.get('name', '')
        # `or ''` (not just the .get default) -- a present-but-NULL column
        # yields None, which the default never replaces.
        url = server.get('url') or ''
        
        if not server_id:
            continue
        
        scanned += 1
        
        package = extract_package_name(url)
        if not package:
            continue
        
        for ecosystem in OSV_ECOSYSTEMS:
            
            vulns = query_osv(ecosystem, package)
            
            for vuln in vulns:
                vuln_id = vuln.get('id', '')
                summary = vuln.get('summary', '')
                severity = vuln.get('severity', [])
                
                if not is_relevant_to_mcp(summary):
                    continue
                
                if threat_already_recorded(server_id, f'osv:{vuln_id}', f'osv:{vuln_id}'):
                    continue
                
                # Default to the keyword severity this module already computes,
                # so a vector-only severity degrades to a real signal instead of
                # a flat 'medium' (or, before FU-189, a ValueError).
                # compute_threat_severity returns 'unknown' (not falsy) when no
                # keyword matches; an OSV hit means a vuln definitely EXISTS, so
                # 'unknown' would understate it -- floor it at 'medium'.
                severity_level = compute_threat_severity(summary)
                if severity_level in (None, '', 'unknown'):
                    severity_level = 'medium'
                for s in (severity or []):
                    if not isinstance(s, dict):
                        continue
                    if not str(s.get('type', '')).upper().startswith('CVSS'):
                        continue
                    score = cvss_base_score(s.get('score'))
                    if score is None:
                        continue
                    if score >= 9.0:
                        severity_level = 'critical'
                    elif score >= 7.0:
                        severity_level = 'high'
                    elif score >= 4.0:
                        severity_level = 'medium'
                    else:
                        severity_level = 'low'
                    break
                
                evidence = f'OSV:{vuln_id} | Summary: {sanitize_for_sql(summary[:500])} | Package: {package} | Ecosystem: {ecosystem}'
                
                try:
                    # ws_write signals TOTAL failure by returning None rather
                    # than raising, so a bare try/except cannot see it. Check
                    # the return value before claiming the row exists. (FU-190)
                    written = ws_write('mcp_threat_associations', {
                        'server_id': server_id,
                        'threat_type': f'osv:{vuln_id}',
                        'severity': severity_level,
                        'evidence': evidence,
                        'source': 'osv',
                    })
                    if written is None:
                        write_failures += 1
                        log(f'NOT RECORDED: OSV vuln {vuln_id} for server {server_id} -- write_service rejected the write')
                    else:
                        vuln_count += 1
                        log(f'Recorded OSV vuln {vuln_id} for server {server_id} (severity: {severity_level})')
                except Exception as e:
                    write_failures += 1
                    log(f'Failed to write OSV vuln for {server_id}: {e}')
        
        if scanned >= 500:
            break
    
    if write_failures:
        log(f'OSV scan complete: scanned {scanned} servers, recorded {vuln_count} vulnerabilities, '
            f'{write_failures} REJECTED by write_service (NOT persisted)')
    else:
        log(f'OSV scan complete: scanned {scanned} servers, recorded {vuln_count} vulnerabilities')
    return vuln_count

def cycle():
    log('='*60)
    log('Starting threat intel ingestion cycle')
    
    article_count = process_world_articles()
    
    osv_count = process_osv_vulns()
    
    log(f'Cycle complete: {article_count} articles, {osv_count} OSV vulnerabilities processed')
    log('='*60)
    
    return {'articles': article_count, 'osv_vulns': osv_count}

def run():
    log(f'{SERVICE_NAME} starting on port {SERVICE_PORT}')
    check_single_instance()
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    create_tables()
    send_heartbeat()
    
    # The priming cycle MUST be as protected as every later one. It used to be
    # a bare `cycle()`, so any first-cycle exception killed the process BEFORE
    # it reached the retry loop below -- the daemon could never survive to use
    # the protection written for it, and the supervisor's only remedy (restart)
    # re-entered the same unprotected line. That is how one TypeError kept this
    # service down for 54 days across 184,818 spawns. (FU-187)
    try:
        cycle()
    except Exception as e:
        log(f'Priming cycle error (continuing into poll loop): {e}')
        traceback.print_exc()
    
    while True:
        time.sleep(POLL_SECS)
        send_heartbeat()
        try:
            cycle()
        except Exception as e:
            log(f'Cycle error: {e}')
            traceback.print_exc()

if __name__ == '__main__':
    run()