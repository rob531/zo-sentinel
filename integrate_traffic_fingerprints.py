import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

from mcp_traffic_fingerprints import detect_mcp_methods, is_mcp_traffic

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'

def ws_write(table, rows, wait=True):
    import requests
    resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': wait}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql):
    import requests
    resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql):
    import requests
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

SERVICE_NAME = 'scanner_traffic_fingerprint_wiring'
LOG_FILE = '/home/workspace/zo_sentinel/logs/scanner_traffic_fingerprint_wiring.log'
POLL_SECS = 300

import os
import time
import signal
import logging

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(SERVICE_NAME)

PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = int(f.read().strip())
        try:
            os.kill(existing, 0)
            log.error(f'Another instance running as PID {existing}. Exiting.')
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f'Acquired PID file: {PID_FILE}')

def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass

def signal_handler(signum, frame):
    log.info(f'Received signal {signum}, shutting down.')
    remove_pid_file()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def send_heartbeat():
    try:
        ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': time.strftime('%Y-%m-%d %H:%M:%S')})
    except Exception as e:
        log.warning(f'Heartbeat failed: {e}')

def write_signal_score(server_id, signal_name, score, evidence):
    rows = {
        'server_id': server_id,
        'signal_name': signal_name,
        'score': score,
        'evidence': evidence,
        'scored_at': time.strftime('%Y-%m-%d %H:%M:%S')
    }
    try:
        ws_write('mcp_signal_scores', rows)
        log.info(f'Wrote signal {signal_name}={score:.3f} for server {server_id}')
    except Exception as e:
        log.error(f'Failed to write signal score: {e}')

def get_pending_scans():
    try:
        result = ws_query("SELECT server_id, name, url FROM mcp_server_registry WHERE verdict IS NULL AND scan_count = 0 LIMIT 50")
        return result.get('rows', [])
    except Exception as e:
        log.error(f'Failed to query pending scans: {e}')
        return []

def analyze_server_traffic(server_id, url):
    try:
        import requests
        headers = {
            'User-Agent': 'MCP-Sentinel/1.0 Security Scanner',
            'Accept': 'application/json, */*'
        }
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        body = response.text
        status_code = response.status_code
        headers_dict = dict(response.headers)
        
        traffic_analysis = {
            'is_mcp_traffic': is_mcp_traffic(body, headers_dict, status_code),
            'detected_methods': detect_mcp_methods(body, headers_dict, status_code),
            'has_mcp_endpoint': response.url if response.url != url else None,
            'status_code': status_code
        }
        
        return traffic_analysis
    except Exception as e:
        log.error(f'Failed to analyze server {server_id} traffic: {e}')
        return None

def process_fingerprint_signals(server_id, analysis):
    if not analysis:
        return
    
    is_mcp = analysis.get('is_mcp_traffic', False)
    detected_methods = analysis.get('detected_methods', [])
    has_mcp_endpoint = analysis.get('has_mcp_endpoint')
    status_code = analysis.get('status_code', 0)
    
    if is_mcp:
        write_signal_score(
            server_id,
            'traffic_mcp_protocol_confirmed',
            1.0,
            f'MCP traffic confirmed via fingerprint analysis. Methods: {detected_methods}'
        )
    else:
        if status_code >= 200 and status_code < 400:
            write_signal_score(
                server_id,
                'traffic_mcp_protocol_confirmed',
                0.1,
                f'Non-MCP traffic detected. Status: {status_code}'
            )
        else:
            write_signal_score(
                server_id,
                'traffic_mcp_protocol_confirmed',
                0.0,
                f'Failed to detect MCP traffic. Status: {status_code}'
            )
    
    if detected_methods:
        method_str = ', '.join(detected_methods)
        write_signal_score(
            server_id,
            'traffic_mcp_methods_detected',
            min(len(detected_methods) / 5.0, 1.0),
            f'Detected MCP methods: {method_str}'
        )
    
    if has_mcp_endpoint:
        write_signal_score(
            server_id,
            'traffic_mcp_endpoint_observed',
            0.8,
            f'MCP endpoint confirmed at: {has_mcp_endpoint}'
        )
    else:
        write_signal_score(
            server_id,
            'traffic_mcp_endpoint_observed',
            0.2,
            'No redirect MCP endpoint detected'
        )

def run_daemon():
    check_single_instance()
    log.info(f'{SERVICE_NAME} starting')
    
    while True:
        try:
            send_heartbeat()
            
            pending = get_pending_scans()
            log.info(f'Found {len(pending)} pending scans')
            
            for server in pending:
                server_id = server.get('server_id')
                url = server.get('url')
                name = server.get('name')
                
                if not server_id or not url:
                    continue
                
                log.info(f'Analyzing traffic for {name} ({server_id})')
                analysis = analyze_server_traffic(server_id, url)
                process_fingerprint_signals(server_id, analysis)
                
                try:
                    ws_execute(f"UPDATE mcp_server_registry SET scan_count = scan_count + 1 WHERE server_id = '{server_id}'")
                except Exception as e:
                    log.error(f'Failed to update scan count: {e}')
            
            time.sleep(POLL_SECS)
        except Exception as e:
            log.error(f'Daemon error: {e}')
            time.sleep(60)

if __name__ == '__main__':
    os.makedirs('/home/workspace/zo_sentinel/logs', exist_ok=True)
    run_daemon()