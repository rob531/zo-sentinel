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
    url = server.get('url', '')
    description = server.get('description', '')
    name = server.get('name', '')
    
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
    description = server.get('description', '')
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
    url = server.get('url', '')
    scan_count = server.get('scan_count', 0)
    registry_source = server.get('registry_source', '')
    
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
    name = server.get('name', '')
    description = server.get('description', '')
    trust_score = server.get('trust_score', 0)
    
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
    url = server.get('url', '')
    registry_source = server.get('registry_source', '')
    
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


def get_servers_needing_signals() -> List[Dict[str, Any]]:
    servers = ws_query('''
        SELECT r.server_id, r.name, r.url, r.description, r.trust_score,
               r.registry_source, r.scan_count, r.verdict, r.metadata
        FROM mcp_server_registry r
        LEFT JOIN (
            SELECT server_id, MAX(scored_at) as latest_score
            FROM mcp_signal_scores
            GROUP BY server_id
        ) latest ON r.server_id = latest.server_id
        WHERE r.verdict IS NULL
           OR r.verdict = ''
           OR latest.latest_score IS NULL
           OR latest.latest_score < CURRENT_TIMESTAMP - INTERVAL '1 hour'
        LIMIT 50
    ''')
    
    if not servers:
        servers = ws_query('''
            SELECT r.server_id, r.name, r.url, r.description, r.trust_score,
                   r.registry_source, r.scan_count, r.verdict, r.metadata
            FROM mcp_server_registry r
            LEFT JOIN (
                SELECT server_id, MAX(scored_at) as latest_score
                FROM mcp_signal_scores
                GROUP BY server_id
            ) latest ON r.server_id = latest.server_id
            ORDER BY latest.latest_score ASC NULLS FIRST
            LIMIT 50
        ''')
    
    return servers


def process_server(server: Dict[str, Any]) -> None:
    server_id = server.get('server_id', '')
    if not server_id:
        return
    
    signals = []
    
    signals.append(compute_url_safety_score(server))
    signals.append(compute_tool_security_score(server))
    signals.append(compute_supply_chain_score(server))
    signals.append(compute_reputation_score(server))
    signals.append(compute_domain_trust_score(server))
    
    composite = compute_composite_score(signals)
    verdict = score_to_verdict(composite)
    
    rows_to_write = []
    for signal in signals:
        rows_to_write.append({
            'id': uuid.uuid4().int % (2**63),
            'server_id': server_id,
            'signal_name': signal['signal_name'],
            'score': signal['score'],
            'evidence': signal['evidence'],
            'scored_at': datetime.utcnow().isoformat()
        })
    
    rows_to_write.append({
        'id': uuid.uuid4().int % (2**63),
            'server_id': server_id,
        'signal_name': 'composite',
        'score': composite,
        'evidence': json.dumps({'verdict': verdict, 'signal_count': len(signals)}),
        'scored_at': datetime.utcnow().isoformat()
    })
    
    ws_write('mcp_signal_scores', rows_to_write)
    
    ws_execute(f'''
        UPDATE mcp_server_registry
        SET trust_score = {composite}, verdict = '{verdict}', last_assessed = '{datetime.utcnow().isoformat()}'
        WHERE server_id = '{server_id}'
    ''')
    
    log(f"Processed {server_id}: verdict={verdict}, score={composite:.1f}, signals={len(signals)}")


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
                log(f"Processing {len(servers)} servers")
                for server in servers:
                    try:
                        process_server(server)
                    except Exception as e:
                        log(f"Error processing server {server.get('server_id')}: {e}")
            else:
                log("No servers need signal processing")
            
        except Exception as e:
            log(f"Cycle error: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    import os
    run()