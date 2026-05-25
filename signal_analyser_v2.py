import sys
import os
import hashlib
import json
import signal
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import requests

sys.path.insert(0, '/home/workspace')
from db_utils import ws_query, ws_write

SERVICE_NAME = 'signal_analyser_v2'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
EXECUTE_URL = 'http://localhost:8772/execute'
QUERY_URL = 'http://localhost:8772/query'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TLS_TIMEOUT = 10
DOMAIN_LOOKUP_TIMEOUT = 15
HTTP_TIMEOUT = 20
OTX_TIMEOUT = 15

THREAT_THRESHOLD = 30
HIGH_RISK_THRESHOLD = 50
REVIEW_THRESHOLD = 70
TRUSTED_RESEARCH_THRESHOLD = 85

DOMAIN_BLACKLIST = [
    'localhost', '127.0.0.1', '0.0.0.0', '::1',
    'example.com', 'test.com', 'localhost.localdomain'
]

KNOWN_BAD_PATTERNS = [
    'eval(atob(', 'base64_decode', 'shell_exec', 'system(',
    'passthru', 'proc_open', 'popen', 'exec(',
    'document.write', 'innerHTML', 'outerHTML',
    'onerror=', 'onclick=', 'javascript:',
    '\\u0000', '\\x00', '%00',
    '../', '..\\', '%2e%2e',
    'sql_injection', 'union select', '1=1', '--',
    'drop table', 'insert into', 'delete from',
    'mimikatz', 'powertool', 'Invoke-Mimikatz',
    'nc -e', '/bin/bash -i', 'bash -i',
]

SIGNAL_WEIGHTS = {
    'tls_validity': 0.15,
    'domain_age': 0.12,
    'github_stars': 0.15,
    'otx_threat_intel': 0.20,
    'tool_count': 0.13,
    'known_bad_pattern': 0.25,
}


def check_single_instance():
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except (ProcessLookupError, ValueError, PermissionError):
            log.warning("Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove PID file on exit."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception as e:
            log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down gracefully...")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def get_write_url() -> str:
    """Return WriteService URL."""
    return f"{WRITE_SERVICE_URL}/write"


def get_query_url() -> str:
    """Return QueryService URL."""
    return QUERY_URL


def get_execute_url() -> str:
    """Return ExecuteService URL."""
    return EXECUTE_URL


def compute_deterministic_id(server_id: str, signal_type: str) -> str:
    """Compute deterministic ID for signal scores."""
    content = f"{server_id}:{signal_type}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def check_tls_validity(url: str) -> float:
    """Check TLS certificate validity. Returns 0-100 score."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        
        if not host or host.lower() in DOMAIN_BLACKLIST:
            return 50.0
        
        import ssl
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        
        with socket.create_connection((host, port), timeout=TLS_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                if not cert:
                    return 40.0
                
                from cryptography import x509
                cert_obj = x509.load_der_x509_certificate(cert)
                not_before = cert_obj.not_valid_before_utc
                not_after = cert_obj.not_valid_after_utc
                now = datetime.now(timezone.utc)
                
                if now < not_before:
                    return 0.0
                if now > not_after:
                    return 0.0
                
                total_days = (not_after - not_before).days
                remaining_days = (not_after - now).days
                
                if remaining_days < 0:
                    return 0.0
                elif remaining_days < 7:
                    return 15.0
                elif remaining_days < 30:
                    return 30.0
                elif remaining_days < 90:
                    return 50.0
                elif remaining_days < 180:
                    return 70.0
                else:
                    return min(100.0, 70.0 + (remaining_days / total_days) * 30.0)
    except ImportError:
        log.warning("cryptography/ssl not available, using HTTP fallback")
        return check_tls_via_http(url)
    except Exception as e:
        log.debug(f"TLS check failed for {url}: {e}")
        return check_tls_via_http(url)


def check_tls_via_http(url: str) -> float:
    """Fallback TLS check via HTTP HEAD request."""
    try:
        response = requests.head(url, timeout=TLS_TIMEOUT, allow_redirects=True)
        if response.status_code >= 400:
            return 20.0
        return 55.0
    except Exception:
        return 35.0


def get_domain_age_days(url: str) -> int:
    """Get domain age in days via WHOIS-like query."""
    try:
        import whois
        parsed = urlparse(url)
        domain = parsed.hostname
        if not domain:
            return 0
        
        w = whois.whois(domain)
        if w and w.creation_date:
            if isinstance(w.creation_date, list):
                creation = w.creation_date[0]
            else:
                creation = w.creation_date
            
            if creation:
                now = datetime.now(timezone.utc)
                if creation.tzinfo is None:
                    creation = creation.replace(tzinfo=timezone.utc)
                return (now - creation).days
    except ImportError:
        log.debug("python-whois not available, using age estimation")
    except Exception as e:
        log.debug(f"WHOIS lookup failed for {url}: {e}")
    
    return estimate_domain_age(url)


def estimate_domain_age(url: str) -> int:
    """Estimate domain age when WHOIS unavailable."""
    try:
        response = requests.get(url, timeout=DOMAIN_LOOKUP_TIMEOUT, 
                               headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
        if 'Last-Modified' in response.headers:
            last_modified = response.headers.get('Last-Modified', '')
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(last_modified)
                now = datetime.now(timezone.utc)
                return (now - dt).days
            except Exception:
                pass
    except Exception:
        pass
    return 30


def score_domain_age(age_days: int) -> float:
    """Score domain age 0-100."""
    if age_days < 0:
        return 0.0
    elif age_days < 7:
        return 5.0
    elif age_days < 30:
        return 20.0
    elif age_days < 90:
        return 40.0
    elif age_days < 180:
        return 60.0
    elif age_days < 365:
        return 75.0
    elif age_days < 730:
        return 85.0
    else:
        return 95.0


def get_github_stars(url: str) -> int:
    """Extract GitHub stars from URL."""
    try:
        parsed = urlparse(url)
        if 'github.com' not in parsed.netloc.lower():
            return 0
        
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2:
            owner, repo = path_parts[0], path_parts[1].replace('.git', '')
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            
            response = requests.get(api_url, timeout=HTTP_TIMEOUT,
                                  headers={'Accept': 'application/vnd.github.v3+json'})
            if response.status_code == 200:
                data = response.json()
                return data.get('stargazers_count', 0) or 0
    except Exception as e:
        log.debug(f"GitHub stars lookup failed for {url}: {e}")
    return 0


def score_github_stars(stars: int) -> float:
    """Score GitHub stars 0-100."""
    if stars == 0:
        return 20.0
    elif stars < 10:
        return 35.0
    elif stars < 50:
        return 50.0
    elif stars < 100:
        return 65.0
    elif stars < 500:
        return 75.0
    elif stars < 1000:
        return 82.0
    elif stars < 5000:
        return 88.0
    else:
        return 95.0


def check_otx_threat_intel(url: str) -> tuple[bool, str]:
    """Check AlienVault OTX threat intelligence. Returns (is_malicious, evidence)."""
    api_key = os.environ.get('Alienvaultapi')
    if not api_key:
        log.debug("AlienVault API key not configured, skipping OTX check")
        return False, "OTX_API_NOT_CONFIGURED"
    
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False, "NO_HOSTNAME"
        
        otx_url = f"https://otx.alienvault.com/api/v1/indicators/hostname/{host}"
        response = requests.get(otx_url, timeout=OTX_TIMEOUT,
                              headers={'X-OTX-API-KEY': api_key,
                                      'Accept': 'application/json'})
        
        if response.status_code == 200:
            data = response.json()
            pulse_count = data.get('pulse_info', {}).get('count', 0)
            
            if pulse_count > 0:
                return True, f"OTX_PULSES:{pulse_count}"
            
            malicious = data.get('malware', {}).get('count', 0)
            if malicious > 0:
                return True, f"OTX_MALWARE:{malicious}"
        
        elif response.status_code == 404:
            return False, "OTX_NOT_FOUND"
        elif response.status_code == 429:
            log.warning("OTX rate limited")
            return False, "OTX_RATE_LIMITED"
            
    except Exception as e:
        log.debug(f"OTX check failed for {url}: {e}")
        return False, f"OTX_ERROR:{type(e).__name__}"
    
    return False, "OTX_CLEAN"


def score_otx_threat(is_malicious: bool, evidence: str) -> float:
    """Score OTX threat intel 0-100."""
    if is_malicious:
        if 'PULSES:10' in evidence or 'MALWARE:5' in evidence:
            return 0.0
        return 5.0
    
    if 'NOT_FOUND' in evidence:
        return 60.0
    elif 'RATE_LIMITED' in evidence or 'ERROR' in evidence:
        return 55.0
    elif evidence == 'OTX_API_NOT_CONFIGURED':
        return 50.0
    else:
        return 80.0


def get_tool_count_from_manifest(server_id: str) -> int:
    """Get tool count from mcp_fingerprints table."""
    try:
        sql = f"""
        SELECT tool_count FROM mcp_fingerprints 
        WHERE server_id = '{server_id}'
        LIMIT 1
        """
        result = ws_query(sql)
        if result and len(result) > 0:
            return result[0].get('tool_count', 0) or 0
    except Exception as e:
        log.debug(f"Tool count lookup failed for {server_id}: {e}")
    return 0


def score_tool_count(tool_count: int) -> float:
    """Score tool count 0-100."""
    if tool_count == 0:
        return 25.0
    elif tool_count < 3:
        return 40.0
    elif tool_count < 10:
        return 60.0
    elif tool_count < 25:
        return 75.0
    elif tool_count < 50:
        return 85.0
    elif tool_count < 100:
        return 90.0
    else:
        return 95.0


def check_known_bad_patterns(description: str, name: str, url: str) -> tuple[bool, str]:
    """Check for known bad patterns in description, name, or URL."""
    combined = f"{description} {name} {url}".lower()
    
    matched_patterns = []
    for pattern in KNOWN_BAD_PATTERNS:
        if pattern.lower() in combined:
            matched_patterns.append(pattern)
    
    if matched_patterns:
        return True, f"KBP:{len(matched_patterns)}|{','.join(matched_patterns[:3])}"
    
    return False, "KBP_CLEAN"


def score_known_bad(is_matched: bool, evidence: str) -> float:
    """Score known bad patterns 0-100 (inverted - matched is bad)."""
    if is_matched:
        count = evidence.count('|') + 1
        if count >= 5:
            return 0.0
        elif count >= 3:
            return 10.0
        elif count >= 2:
            return 25.0
        else:
            return 35.0
    
    return 85.0


def compute_trust_score(signal_scores: Dict[str, float]) -> float:
    """Compute weighted trust score from individual signals."""
    total_score = 0.0
    total_weight = 0.0
    
    for signal_name, score in signal_scores.items():
        weight = SIGNAL_WEIGHTS.get(signal_name, 0.1)
        total_score += score * weight
        total_weight += weight
    
    if total_weight > 0:
        return round(total_score / total_weight, 2)
    
    return 50.0


def score_to_verdict(score: float) -> str:
    """Convert numeric score to verdict string."""
    if score < THREAT_THRESHOLD:
        return 'BLOCKED'
    elif score < HIGH_RISK_THRESHOLD:
        return 'HIGH_RISK'
    elif score < REVIEW_THRESHOLD:
        return 'REVIEW'
    elif score < TRUSTED_RESEARCH_THRESHOLD:
        return 'TRUSTED_RESEARCH'
    else:
        return 'TRUSTED'


def score_to_risk_tier(score: float) -> str:
    """Convert score to risk tier."""
    if score < THREAT_THRESHOLD:
        return 'CRITICAL'
    elif score < HIGH_RISK_THRESHOLD:
        return 'HIGH'
    elif score < REVIEW_THRESHOLD:
        return 'MEDIUM'
    elif score < TRUSTED_RESEARCH_THRESHOLD:
        return 'LOW'
    else:
        return 'MINIMAL'


def write_signal_score(server_id: str, signal_type: str, score: float, 
                       evidence: str, computed_at: str) -> bool:
    """Write a signal score to mcp_signal_scores via WriteService."""
    score_id = compute_deterministic_id(server_id, signal_type)
    
    row = {
        'server_id': server_id,
        'signal_name': signal_type,
        'score': round(score, 2),
        'evidence': evidence,
        'computed_at': computed_at,
    }
    
    try:
        ws_write('mcp_signal_scores', [row])
        return True
    except Exception as e:
        log.error(f"Failed to write signal score for {server_id}/{signal_type}: {e}")
        return False


def get_unscored_servers(limit: int = 100) -> List[Dict[str, Any]]:
    """Get servers that need scoring."""
    try:
        sql = f"""
        SELECT server_id, name, url, description, trust_score, verdict
        FROM mcp_server_registry
        WHERE verdict IS NULL OR verdict = 'unknown'
        ORDER BY scan_count ASC, first_seen ASC
        LIMIT {limit}
        """
        result = ws_query(sql)
        return result if result else []
    except Exception as e:
        log.error(f"Failed to get unscored servers: {e}")
        return []


def get_servers_needing_rescore(limit: int = 50) -> List[Dict[str, Any]]:
    """Get servers needing signal refresh."""
    try:
        sql = f"""
        SELECT r.server_id, r.name, r.url, r.description, r.trust_score, r.verdict
        FROM mcp_server_registry r
        LEFT JOIN mcp_signal_scores s ON r.server_id = s.server_id
        WHERE r.verdict IS NOT NULL AND r.verdict != 'unknown'
        GROUP BY r.server_id
        HAVING MAX(s.computed_at) < NOW() - INTERVAL '7 days' 
           OR COUNT(s.signal_name) < 6
        ORDER BY MAX(s.computed_at) ASC NULLS FIRST
        LIMIT {limit}
        """
        result = ws_query(sql)
        return result if result else []
    except Exception as e:
        log.warning(f"Failed to get servers needing rescore (fallback to unscored): {e}")
        return get_unscored_servers(limit)


def process_server(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single server and compute all signals."""
    server_id = server['server_id']
    url = server['url'] or ''
    name = server['name'] or ''
    description = server['description'] or ''
    
    if not url:
        log.debug(f"Skipping {server_id}: no URL")
        return None
    
    now = utc_now_iso()
    signal_scores = {}
    all_success = True
    
    try:
        tls_score = check_tls_validity(url)
        signal_scores['tls_validity'] = tls_score
        write_signal_score(server_id, 'tls_validity', tls_score, 
                          f"tls_score:{tls_score}", now)
    except Exception as e:
        log.warning(f"tls_validity failed for {server_id}: {e}")
        signal_scores['tls_validity'] = 50.0
        all_success = False
    
    try:
        domain_age = get_domain_age_days(url)
        age_score = score_domain_age(domain_age)
        signal_scores['domain_age'] = age_score
        write_signal_score(server_id, 'domain_age', age_score,
                          f"age_days:{domain_age}", now)
    except Exception as e:
        log.warning(f"domain_age failed for {server_id}: {e}")
        signal_scores['domain_age'] = 50.0
        all_success = False
    
    try:
        github_stars = get_github_stars(url)
        stars_score = score_github_stars(github_stars)
        signal_scores['github_stars'] = stars_score
        write_signal_score(server_id, 'github_stars', stars_score,
                          f"github_stars:{github_stars}", now)
    except Exception as e:
        log.warning(f"github_stars failed for {server_id}: {e}")
        signal_scores['github_stars'] = 50.0
        all_success = False
    
    try:
        is_malicious, otx_evidence = check_otx_threat_intel(url)
        otx_score = score_otx_threat(is_malicious, otx_evidence)
        signal_scores['otx_threat_intel'] = otx_score
        write_signal_score(server_id, 'otx_threat_intel', otx_score,
                          otx_evidence, now)
    except Exception as e:
        log.warning(f"otx_threat_intel failed for {server_id}: {e}")
        signal_scores['otx_threat_intel'] = 50.0
        all_success = False
    
    try:
        tool_count = get_tool_count_from_manifest(server_id)
        tool_score = score_tool_count(tool_count)
        signal_scores['tool_count'] = tool_score
        write_signal_score(server_id, 'tool_count', tool_score,
                          f"tool_count:{tool_count}", now)
    except Exception as e:
        log.warning(f"tool_count failed for {server_id}: {e}")
        signal_scores['tool_count'] = 50.0
        all_success = False
    
    try:
        is_bad, kbp_evidence = check_known_bad_patterns(description, name, url)
        kbp_score = score_known_bad(is_bad, kbp_evidence)
        signal_scores['known_bad_pattern'] = kbp_score
        write_signal_score(server_id, 'known_bad_pattern', kbp_score,
                          kbp_evidence, now)
    except Exception as e:
        log.warning(f"known_bad_pattern failed for {server_id}: {e}")
        signal_scores['known_bad_pattern'] = 85.0
        all_success = False
    
    trust_score = compute_trust_score(signal_scores)
    verdict = score_to_verdict(trust_score)
    risk_tier = score_to_risk_tier(trust_score)
    
    if not all_success:
        verdict = 'REVIEW'
        log.info(f"Server {server_id} fell back to REVIEW due to signal fetch failures")
    
    return {
        'server_id': server_id,
        'trust_score': trust_score,
        'verdict': verdict,
        'risk_tier': risk_tier,
        'signal_scores': signal_scores,
        'computed_at': now,
    }


def update_server_verdict(server_id: str, trust_score: float, 
                          verdict: str, risk_tier: str, computed_at: str) -> bool:
    """Update server verdict in registry."""
    try:
        sql = f"""
        UPDATE mcp_server_registry 
        SET trust_score = {trust_score},
            verdict = '{verdict}',
            last_scanned = '{computed_at}'
        WHERE server_id = '{server_id}'
        """
        response = requests.post(get_execute_url(), 
                                 json={'sql': sql}, 
                                 timeout=HTTP_TIMEOUT)
        if response.status_code in (200, 201):
            return True
        log.warning(f"Failed to update verdict for {server_id}: {response.text}")
        return False
    except Exception as e:
        log.error(f"Failed to update server verdict: {e}")
        return False


def ensure_signal_scores_table() -> bool:
    """Ensure mcp_signal_scores table exists."""
    try:
        sql = """
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            computed_at TIMESTAMPTZ,
            UNIQUE(server_id, signal_name)
        )
        """
        response = requests.post(get_execute_url(),
                                json={'sql': sql},
                                timeout=HTTP_TIMEOUT)
        return response.status_code in (200, 201)
    except Exception as e:
        log.error(f"Failed to create mcp_signal_scores table: {e}")
        return False


def cycle() -> int:
    """Perform one scoring cycle. Returns number of servers processed."""
    log.info("Starting signal analysis cycle")
    
    ensure_signal_scores_table()
    
    servers = get_unscored_servers(50)
    if not servers:
        servers = get_servers_needing_rescore(50)
    
    if not servers:
        log.info("No servers to process")
        return 0
    
    log.info(f"Processing {len(servers)} servers")
    processed = 0
    verdict_counts = {}
    
    for server in servers:
        server_id = server.get('server_id', 'unknown')
        try:
            result = process_server(server)
            if result:
                update_server_verdict(
                    server_id,
                    result['trust_score'],
                    result['verdict'],
                    result['risk_tier'],
                    result['computed_at']
                )
                verdict_counts[result['verdict']] = verdict_counts.get(result['verdict'], 0) + 1
                processed += 1
                log.info(f"Scored {server_id}: {result['verdict']} ({result['trust_score']})")
        except Exception as e:
            log.error(f"Failed to process {server_id}: {e}")
    
    log.info(f"Cycle complete: {processed} servers, verdicts: {verdict_counts}")
    return processed


def send_heartbeat():
    """Send heartbeat to service_health table."""
    try:
        row = {
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso(),
            'status': 'running',
            'meta': json.dumps({'version': 'v2', 'cycle_interval': POLL_SECS})
        }
        ws_write('service_health', [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


POLL_SECS = 60


def run():
    """Main run loop."""
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_signal_scores_table()
    
    try:
        while True:
            try:
                processed = cycle()
                send_heartbeat()
            except Exception as e:
                log.error(f"Cycle error: {e}")
            
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("Received keyboard interrupt")
    finally:
        remove_pid_file()


if __name__ == '__main__':
    import socket
    run()