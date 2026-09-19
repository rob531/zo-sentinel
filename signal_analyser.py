#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/workspace')
import json
import hashlib
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import requests
try:
    from enrichers.discrimination_enrichers import compute_tool_description_safety, compute_temporal_stability
except ImportError:
    compute_tool_description_safety = None
    compute_temporal_stability = None

SERVICE_NAME = 'signal_analyser'
SERVICE_PORT = 8778
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_SERVICE = 'http://127.0.0.1:8772'
EXECUTE_SERVICE = 'http://127.0.0.1:8772'
WRITE_URL = f'{WRITE_SERVICE}/write'
QUERY_URL = f'{QUERY_SERVICE}/query'
EXECUTE_URL = f'{EXECUTE_SERVICE}/execute'
POLL_SECS = 30

# Rescore cadence.
#
# These signals are deterministic functions of static registry fields (name,
# url, description, registry_source, metadata). Rescoring an unchanged row
# cannot change its score, so the old 1-hour TTL rewrote the entire registry
# roughly every 35 minutes and grew mcp_signal_scores to 2.4M rows for 3,173
# servers -- an average of 778 identical rows each.
#
# RESCORE_TTL_HOURS is the soft floor; INPUT_HASH is the real gate. MAX_AGE_DAYS
# guarantees every server is eventually refreshed even if its inputs never
# change, so a scoring-logic change still propagates.
RESCORE_TTL_HOURS = 24
MAX_AGE_DAYS = 7
HASH_FIELDS = ('name', 'url', 'description', 'registry_source', 'metadata')
LOG_FILE = '/home/workspace/logs/signal_analyser.log'

VERDICT_THRESHOLDS = {
    'BLOCKED': 30,
    'HIGH_RISK': 50,
    'REVIEW': 70,
    'TRUSTED_RESEARCH': 85,
}

VERDICT_ORDER = ['BLOCKED', 'HIGH_RISK', 'REVIEW', 'TRUSTED_RESEARCH', 'TRUSTED']

SIGNAL_WEIGHTS = {
    'url_safety': 0.20,
    'tool_security': 0.25,
    'supply_chain': 0.20,
    'reputation': 0.20,
    'domain_trust': 0.15,
}

HTTP_TIMEOUT = 10


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {msg}", flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def check_single_instance() -> bool:
    import os
    pid = str(os.getpid())
    try:
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing != pid:
            log(f"Instance already running with PID {existing}, exiting")
            return False
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"Error checking PID: {e}")
    try:
        with open(PID_FILE, 'w') as f:
            f.write(pid)
    except Exception as e:
        log(f"Error writing PID: {e}")
    return True


def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down gracefully")
    import os
    try:
        os.remove(PID_FILE)
    except Exception:
        pass
    sys.exit(0)


def remove_pid_file():
    import os
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def get_write_url() -> str:
    return WRITE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    for attempt in range(3):
        try:
            resp = requests.post(WRITE_URL, json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code in (200, 201):
                return True
            log(f"ws_write attempt {attempt+1} failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log(f"ws_write attempt {attempt+1} exception: {e}")
        time.sleep(1)
    return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    payload = {'sql': sql}
    for attempt in range(3):
        try:
            resp = requests.post(QUERY_URL, json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('rows', [])
            log(f"ws_query attempt {attempt+1} failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log(f"ws_query attempt {attempt+1} exception: {e}")
        time.sleep(1)
    return []


def ws_execute(sql: str) -> bool:
    payload = {'sql': sql}
    for attempt in range(3):
        try:
            resp = requests.post(EXECUTE_URL, json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code in (200, 201):
                return True
            log(f"ws_execute attempt {attempt+1} failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log(f"ws_execute attempt {attempt+1} exception: {e}")
        time.sleep(1)
    return False


def send_heartbeat() -> None:
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.utcnow().isoformat()
        }])
    except Exception as e:
        log(f"Heartbeat failed: {e}")


def ensure_tables() -> None:
    ws_execute('''
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at VARCHAR
        )
    ''')


def compute_url_safety_score(server: Dict[str, Any]) -> Dict[str, Any]:
    url = (server.get('url') or '')
    description = (server.get('description') or '')
    name = (server.get('name') or '')
    
    score = 70.0
    evidence_parts = []
    
    if not url:
        score -= 20
        evidence_parts.append("no_url")
    elif not url.startswith('https://'):
        score -= 15
        evidence_parts.append("no_https")
    
    suspicious_tlds = ['.xyz', '.top', '.pw', '.tk', '.ml', '.ga', '.cf', '.gq', '.buzz']
    for tld in suspicious_tlds:
        if url.lower().endswith(tld):
            score -= 10
            evidence_parts.append(f"suspicious_tld:{tld}")
            break
    
    injection_patterns = [
        'eval(', 'exec(', 'base64', 'decode', 'obfuscate',
        'password', 'credential', 'secret', 'api_key', 'apikey'
    ]
    combined = (description + name + url).lower()
    for pattern in injection_patterns:
        if pattern.lower() in combined:
            score -= 5
            evidence_parts.append(f"pattern:{pattern}")
    
    score = max(0.0, min(100.0, score))
    return {
        'signal_name': 'url_safety',
        'score': score,
        'evidence': json.dumps({'checks': evidence_parts, 'base_score': score})
    }


def compute_tool_security_score(server: Dict[str, Any]) -> Dict[str, Any]:
    # Use new discrimination enricher if available
    if compute_tool_description_safety is not None:
        result = compute_tool_description_safety(server)
        evid = result.get('evidence', [])
        ev_str = '; '.join(evid[:3]) if isinstance(evid, list) else str(evid)[:200]
        return {'signal_name': 'tool_security', 'score': float(result['score']), 'evidence': ev_str}
    # Fallback: legacy logic
    description = (server.get('description') or '')
    tools = []  # tools column not in registry schema
    score = 70.0
    evidence_parts = []
    if not tools:
        score -= 10
        evidence_parts.append("no_tools_defined")
    else:
        tool_count = len(tools)
        if tool_count > 50:
            score += 5
            evidence_parts.append(f"high_tool_count:{tool_count}")
        
        dangerous_patterns = ['delete', 'drop', 'rm ', 'remove', 'destroy', 'exec', 'run_shell']
        for tool in tools:
            tool_str = str(tool).lower()
            for pattern in dangerous_patterns:
                if pattern in tool_str:
                    score -= 5
                    evidence_parts.append(f"dangerous_tool:{pattern}")
                    break
    
    if not description:
        score -= 10
        evidence_parts.append("no_description")
    elif len(description) < 50:
        score -= 5
        evidence_parts.append("short_description")
    
    score = max(0.0, min(100.0, score))
    return {
        'signal_name': 'tool_security',
        'score': score,
        'evidence': json.dumps({'checks': evidence_parts, 'tool_count': len(tools)})
    }


def compute_supply_chain_score(server: Dict[str, Any]) -> Dict[str, Any]:
    url = (server.get('url') or '')
    # `.get(k, 0)` returns 0 only when the key is ABSENT. scan_count is a
    # nullable column, so the key is present with value None and the default
    # never applies -- which raised TypeError on the comparisons below for
    # ~38% of every cycle (7,199 logged before this fix).
    scan_count = server.get('scan_count') or 0
    registry_source = (server.get('registry_source') or '')
    
    score = 70.0
    evidence_parts = []
    
    if scan_count > 100:
        score += 15
        evidence_parts.append(f"high_scan_count:{scan_count}")
    elif scan_count > 50:
        score += 10
        evidence_parts.append(f"medium_scan_count:{scan_count}")
    elif scan_count > 0:
        score += 5
        evidence_parts.append(f"low_scan_count:{scan_count}")
    else:
        score -= 10
        evidence_parts.append("never_scanned")
    
    trusted_sources = ['npm', 'github', 'anthropic', 'smithery']
    if any(s in registry_source.lower() for s in trusted_sources):
        score += 10
        evidence_parts.append(f"trusted_source:{registry_source}")
    
    if 'npmjs.com' in url or 'github.com' in url:
        score += 5
        evidence_parts.append("major_registry")
    
    score = max(0.0, min(100.0, score))
    return {
        'signal_name': 'supply_chain',
        'score': score,
        'evidence': json.dumps({'checks': evidence_parts, 'scan_count': scan_count})
    }


def compute_reputation_score(server: Dict[str, Any]) -> Dict[str, Any]:
    name = (server.get('name') or '')
    description = (server.get('description') or '')
    trust_score = (server.get('trust_score') or 0)
    
    score = 70.0
    evidence_parts = []
    
    if trust_score > 80:
        score = trust_score
        evidence_parts.append(f"existing_trust:{trust_score}")
    elif trust_score > 50:
        score = trust_score
        evidence_parts.append(f"moderate_trust:{trust_score}")
    elif trust_score > 0:
        score = trust_score * 0.8
        evidence_parts.append(f"low_trust:{trust_score}")
    else:
        score = 70.0
        evidence_parts.append("no_existing_trust")
    
    if len(name) < 3:
        score -= 10
        evidence_parts.append("suspicious_name_length")
    
    if description and len(description) > 200:
        score += 5
        evidence_parts.append("detailed_description")
    
    score = max(0.0, min(100.0, score))
    return {
        'signal_name': 'reputation',
        'score': score,
        'evidence': json.dumps({'checks': evidence_parts, 'existing_trust': trust_score})
    }


def compute_domain_trust_score(server: Dict[str, Any]) -> Dict[str, Any]:
    url = (server.get('url') or '')
    registry_source = (server.get('registry_source') or '')
    
    score = 70.0
    evidence_parts = []
    
    if not url:
        score -= 20
        evidence_parts.append("no_url_for_domain")
    else:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            trusted_domains = [
                'github.com', 'npmjs.com', 'pypi.org', 'hub.docker.com',
                'registry.npmjs.com', 'cdn.jsdelivr.net', 'unpkg.com',
                'raw.githubusercontent.com', 'api.github.com'
            ]
            for trusted in trusted_domains:
                if trusted in domain:
                    score += 20
                    evidence_parts.append(f"trusted_domain:{trusted}")
                    break
            else:
                if 'localhost' in domain or '127.0.0.1' in domain:
                    score -= 15
                    evidence_parts.append("localhost_domain")
                elif domain.startswith('192.168.') or domain.startswith('10.'):
                    score -= 10
                    evidence_parts.append("private_ip_domain")
                else:
                    score -= 5
                    evidence_parts.append(f"unknown_domain:{domain[:30]}")
        except Exception as e:
            score -= 10
            evidence_parts.append(f"domain_parse_error:{str(e)}")
    
    if registry_source == 'npm':
        score += 5
        evidence_parts.append("npm_registry_source")
    elif registry_source == 'github':
        score += 5
        evidence_parts.append("github_registry_source")
    
    score = max(0.0, min(100.0, score))
    return {
        'signal_name': 'domain_trust',
        'score': score,
        'evidence': json.dumps({'checks': evidence_parts})
    }


def score_to_verdict(score: float) -> str:
    if score >= 85:
        return 'TRUSTED'
    elif score >= 70:
        return 'TRUSTED_RESEARCH'
    elif score >= 50:
        return 'REVIEW'
    elif score >= 30:
        return 'HIGH_RISK'
    else:
        return 'BLOCKED'


def compute_composite_score(signals: List[Dict[str, Any]]) -> float:
    total_weight = 0.0
    weighted_sum = 0.0
    
    for signal in signals:
        name = signal.get('signal_name', '')
        score = signal.get('score', 0.0)
        weight = SIGNAL_WEIGHTS.get(name, 0.0)
        weighted_sum += score * weight
        total_weight += weight
    
    if total_weight == 0:
        return 70.0
    
    return weighted_sum / total_weight


def sql_quote(value: str) -> str:
    """Escape a value for single-quoted SQL. server_ids are registry-supplied
    and contain slashes and, occasionally, quotes."""
    return str(value).replace("'", "''")


def input_hash(server: Dict[str, Any]) -> str:
    """Fingerprint the fields the signals actually read.

    If this is unchanged since the last scoring pass, recomputing the signals
    is guaranteed to produce the same numbers, so the pass is skipped.
    """
    material = json.dumps(
        {k: server.get(k) for k in HASH_FIELDS}, sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]


def last_score_state(server_id: str) -> Dict[str, Any]:
    """Return {input_hash, scored_at} from this server's most recent composite."""
    rows = ws_query(f"""
        SELECT evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{sql_quote(server_id)}' AND signal_name = 'composite'
        ORDER BY scored_at DESC
        LIMIT 1
    """)
    if not rows:
        return {}
    try:
        ev = json.loads(rows[0].get('evidence') or '{}')
    except (ValueError, TypeError):
        ev = {}
    return {'input_hash': ev.get('input_hash'), 'scored_at': rows[0].get('scored_at')}


def get_servers_needing_signals() -> List[Dict[str, Any]]:
    """Servers whose scores are missing or past the TTL.

    There is deliberately NO unconditional fallback here. The previous version
    fell through to `ORDER BY latest_score ASC NULLS FIRST LIMIT 50` with no
    WHERE clause, so an empty result from the staleness query still returned 50
    servers to rescore. "Nothing to do" was treated as "do the oldest anyway",
    the idle branch in run() became unreachable (measured: 0 occurrences of
    "No servers need signal processing" against 209 "Processing 50 servers"),
    and the daemon rewrote the registry continuously.

    An empty list is now a real answer, and the caller idles on it.
    """
    return ws_query(f'''
        SELECT r.server_id, r.name, r.url, r.description, r.trust_score,
               r.registry_source, r.scan_count, r.verdict, r.metadata
        FROM mcp_server_registry r
        LEFT JOIN (
            SELECT server_id, MAX(scored_at) as latest_score
            FROM mcp_signal_scores
            GROUP BY server_id
        ) latest ON r.server_id = latest.server_id
        WHERE r.verdict IS NULL
           OR r.verdict = \'\'
           OR latest.latest_score IS NULL
           OR latest.latest_score < CURRENT_TIMESTAMP - INTERVAL \'{RESCORE_TTL_HOURS} hour\'
        ORDER BY latest.latest_score ASC NULLS FIRST
        LIMIT 50
    ''')


def process_server(server: Dict[str, Any]) -> str:
    """Score one server. Returns 'scored', 'skipped' or 'noop'."""
    server_id = (server.get('server_id') or '')
    if not server_id:
        return 'noop'

    # Skip when the inputs the signals read have not changed. Without this the
    # TTL alone decides cadence, and a TTL is a guess about how often facts
    # change; the hash is a measurement of whether they did.
    current_hash = input_hash(server)
    prev = last_score_state(server_id)
    if prev.get('input_hash') and prev['input_hash'] == current_hash:
        scored_at = prev.get('scored_at')
        if scored_at and not _older_than_days(scored_at, MAX_AGE_DAYS):
            return 'skipped'

    signals = []

    signals.append(compute_url_safety_score(server))
    signals.append(compute_tool_security_score(server))
    signals.append(compute_supply_chain_score(server))
    signals.append(compute_reputation_score(server))
    signals.append(compute_domain_trust_score(server))

    composite = compute_composite_score(signals)
    verdict = score_to_verdict(composite)

    now = datetime.utcnow().isoformat()
    rows_to_write = []
    for signal in signals:
        rows_to_write.append({
            'id': uuid.uuid4().int % (2**63),
            'server_id': server_id,
            'signal_name': signal['signal_name'],
            'score': signal['score'],
            'evidence': signal['evidence'],
            'scored_at': now
        })

    rows_to_write.append({
        'id': uuid.uuid4().int % (2**63),
        'server_id': server_id,
        'signal_name': 'composite',
        'score': composite,
        'evidence': json.dumps({
            'verdict': verdict,
            'signal_count': len(signals),
            'input_hash': current_hash,
        }),
        'scored_at': now
    })

    # Replace, do not append. One row per server per signal per day. The old
    # code appended on every pass with a fresh uuid, which is how this table
    # reached 2.4M rows for 3,173 servers. Day-granularity keeps a usable
    # history for drift work without unbounded growth.
    ws_execute(f"""
        DELETE FROM mcp_signal_scores
        WHERE server_id = '{sql_quote(server_id)}'
          AND scored_at >= CAST(current_date AS TIMESTAMP WITH TIME ZONE)
    """)

    ws_write('mcp_signal_scores', rows_to_write)

    ws_execute(f'''
        UPDATE mcp_server_registry
        SET trust_score = {composite}, verdict = '{sql_quote(verdict)}', last_assessed = '{now}'
        WHERE server_id = '{sql_quote(server_id)}'
    ''')

    log(f"Processed {server_id}: verdict={verdict}, score={composite:.1f}, signals={len(signals)}")
    return 'scored'


def _older_than_days(scored_at: Any, days: int) -> bool:
    """True when scored_at is older than `days`, or unparseable (fail toward
    rescoring rather than toward silently never refreshing)."""
    try:
        text = str(scored_at).replace('Z', '+00:00')
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return (datetime.utcnow() - ts) > timedelta(days=days)
    except (ValueError, TypeError):
        return True


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        return
    
    log(f"Signal analyser starting, PID={os.getpid()}")
    
    ensure_tables()
    
    while True:
        try:
            send_heartbeat()
            
            servers = get_servers_needing_signals()
            
            if servers:
                scored = skipped = 0
                for server in servers:
                    try:
                        outcome = process_server(server)
                        if outcome == 'scored':
                            scored += 1
                        elif outcome == 'skipped':
                            skipped += 1
                    except Exception as e:
                        log(f"Error processing server {server.get('server_id')}: {e}")
                log(f"Cycle: {len(servers)} candidates, {scored} scored, "
                    f"{skipped} unchanged (inputs identical)")
            else:
                log("No servers need signal processing")
            
        except Exception as e:
            log(f"Cycle error: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    import os
    run()