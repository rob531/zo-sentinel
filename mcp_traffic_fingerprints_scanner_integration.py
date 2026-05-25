import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/mcp_traffic_fingerprints_scanner_integration.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'mcp_traffic_fingerprints_scanner_integration'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
SERVICE_PORT = None
PID_FILE = '/tmp/mcp_traffic_fingerprints_scanner_integration.pid'
POLL_SECS = 60
FINGERPRINTS_MODULE_PATH = '/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py'
SCANNER_MODULE_PATH = '/home/workspace/zo_sentinel/mcp_scanner.py'

_mcp_fingerprints = None
_scanner_module = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error('Another instance already running with PID %d', old_pid)
            sys.exit(1)
        except OSError:
            logger.info('Stale PID file removed')
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def signal_handler(signum: int, frame) -> None:
    logger.info('Received signal %d, shutting down gracefully', signum)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            f'{QUERY_SERVICE_URL}/query',
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error('ws_write failed for table %s: %s', table, e)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            f'{EXECUTE_SERVICE_URL}/execute',
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error('ws_execute failed: %s', e)
        return False


def send_heartbeat(status: str = 'ok', meta: Optional[Dict[str, Any]] = None) -> None:
    ts = utc_now_iso()
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [row])


def load_fingerprints_module() -> Optional[Any]:
    global _mcp_fingerprints
    if _mcp_fingerprints is not None:
        return _mcp_fingerprints
    if not os.path.exists(FINGERPRINTS_MODULE_PATH):
        logger.error('mcp_traffic_fingerprints.py not found at %s', FINGERPRINTS_MODULE_PATH)
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('mcp_traffic_fingerprints', FINGERPRINTS_MODULE_PATH)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules['mcp_traffic_fingerprints'] = module
            spec.loader.exec_module(module)
            _mcp_fingerprints = module
            logger.info('Loaded mcp_traffic_fingerprints module successfully')
            return _mcp_fingerprints
    except Exception as e:
        logger.error('Failed to load mcp_traffic_fingerprints: %s', e)
    return None


def check_scanner_exists() -> bool:
    if not os.path.exists(SCANNER_MODULE_PATH):
        logger.error('mcp_scanner.py not found at %s', SCANNER_MODULE_PATH)
        return False
    return True


def ensure_mcp_protocol_confirmed_signal_type() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        server_id VARCHAR,
        signal_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        scored_at TIMESTAMPTZ,
        PRIMARY KEY (server_id, signal_name)
    )
    """
    ws_execute(sql)


def get_unprocessed_candidates_for_protocol_check(limit: int = 100) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.url,
        r.description,
        r.first_seen,
        r.last_seen
    FROM mcp_server_registry r
    WHERE r.verdict = 'unknown'
    AND r.registry_source = 'candidate'
    ORDER BY r.last_seen DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def fetch_response_body(url: str, timeout: int = 10) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
        content = resp.text
        if len(content) > 50000:
            content = content[:50000]
        return content
    except Exception as e:
        logger.debug('Failed to fetch %s: %s', url, e)
        return None


def analyze_protocol_confidence(fp_module: Any, response_body: str, url: str) -> tuple:
    confidence = 0.0
    evidence_details = {}
    detected_methods = []
    is_mcp = False
    
    try:
        if hasattr(fp_module, 'detect_mcp_methods'):
            methods = fp_module.detect_mcp_methods(response_body, url)
            if methods:
                detected_methods = methods
                evidence_details['detected_methods'] = methods
                
        if hasattr(fp_module, 'is_mcp_traffic'):
            is_mcp = fp_module.is_mcp_traffic(response_body, url)
            evidence_details['is_mcp_traffic'] = is_mcp
            
        session_indicators = []
        if hasattr(fp_module, 'extract_session_indicators'):
            session_indicators = fp_module.extract_session_indicators(response_body, url)
            if session_indicators:
                evidence_details['session_indicators'] = session_indicators[:5]
        
        if is_mcp and detected_methods:
            confidence = min(0.95, 0.5 + (len(detected_methods) * 0.15))
            if session_indicators:
                confidence = min(0.95, confidence + 0.2)
        elif is_mcp:
            confidence = 0.4
        elif detected_methods:
            confidence = 0.3
            
        if not response_body or len(response_body) < 50:
            confidence = min(confidence, 0.1)
            
    except Exception as e:
        logger.debug('Error analyzing protocol for %s: %s', url, e)
        
    return confidence, is_mcp, evidence_details


def write_protocol_confirmed_signal(
    server_id: str,
    confidence: float,
    evidence_details: Dict[str, Any],
    is_mcp: bool
) -> bool:
    signal_name = 'MCP_PROTOCOL_CONFIRMED'
    scored_at = utc_now_iso()
    
    evidence_blob = {
        'signal_type': signal_name,
        'confidence': confidence,
        'is_mcp_protocol': is_mcp,
        'evidence_blob': evidence_details
    }
    
    evidence_json = str(evidence_blob).replace("'", "''")
    
    sql = f"""
    INSERT INTO mcp_signal_scores (server_id, signal_name, score, evidence, scored_at)
    VALUES ('{server_id}', '{signal_name}', {confidence}, '{evidence_json}', '{scored_at}')
    ON CONFLICT (server_id, signal_name) DO UPDATE SET
        score = {confidence},
        evidence = '{evidence_json}',
        scored_at = '{scored_at}'
    """
    
    return ws_execute(sql)


def update_registry_verdict_if_confirmed(server_id: str, confidence: float) -> None:
    if confidence >= 0.5:
        sql = f"""
        UPDATE mcp_server_registry
        SET verdict = 'AMBER_UNVERIFIED'
        WHERE server_id = '{server_id}' AND verdict = 'unknown'
        """
        ws_execute(sql)


def process_candidates(fp_module: Any) -> Dict[str, Any]:
    results = {
        'processed': 0,
        'confirmed': 0,
        'failed': 0,
        'low_confidence': 0
    }
    
    candidates = get_unprocessed_candidates_for_protocol_check(limit=50)
    if not candidates:
        logger.debug('No candidates to process')
        return results
        
    logger.info('Processing %d candidate servers for MCP protocol confirmation', len(candidates))
    
    for candidate in candidates:
        server_id = candidate.get('server_id', '')
        url = candidate.get('url', '')
        name = candidate.get('name', '')
        
        if not url:
            results['failed'] += 1
            continue
            
        response_body = fetch_response_body(url, timeout=10)
        if not response_body:
            results['failed'] += 1
            continue
            
        confidence, is_mcp, evidence_details = analyze_protocol_confidence(
            fp_module, response_body, url
        )
        
        if write_protocol_confirmed_signal(server_id, confidence, evidence_details, is_mcp):
            results['processed'] += 1
            if confidence >= 0.5:
                results['confirmed'] += 1
                update_registry_verdict_if_confirmed(server_id, confidence)
                logger.info('MCP protocol confirmed for %s (confidence: %.2f)', name, confidence)
            else:
                results['low_confidence'] += 1
                logger.debug('Low confidence for %s (%.2f)', name, confidence)
        else:
            results['failed'] += 1
            
    return results


def cycle() -> Dict[str, Any]:
    fp_module = load_fingerprints_module()
    if fp_module is None:
        logger.error('Cannot load mcp_traffic_fingerprints module, skipping cycle')
        return {'status': 'error', 'reason': 'module_load_failed'}
    
    if not check_scanner_exists():
        logger.error('mcp_scanner.py not found, cannot wire integration')
        return {'status': 'error', 'reason': 'scanner_not_found'}
    
    ensure_mcp_protocol_confirmed_signal_type()
    
    results = process_candidates(fp_module)
    return {'status': 'ok', 'results': results}


def run() -> None:
    logger.info('Starting %s', SERVICE_NAME)
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        while True:
            try:
                result = cycle()
                if result.get('status') == 'ok':
                    meta = {'last_cycle': utc_now_iso(), 'results': result.get('results', {})}
                    send_heartbeat('ok', meta)
                    logger.info('Cycle complete: %s', result.get('results', {}))
                else:
                    send_heartbeat('degraded', {'last_cycle': utc_now_iso(), 'reason': result.get('reason', 'unknown')})
            except Exception as e:
                logger.error('Cycle error: %s', e)
                send_heartbeat('error', {'error': str(e), 'ts': utc_now_iso()})
            
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        logger.info('Interrupted by user')
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()