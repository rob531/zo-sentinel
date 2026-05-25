import logging
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger('diagnose_stale_daemon_cluster')

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
HEALTH_TABLE = 'service_health'
STALE_THRESHOLD_MINUTES = 5

DAEMONS = [
    'anti_entropy',
    'wisdom_synthesiser', 
    'write_service',
    'mcp_scanner',
    'attestation_engine'
]

def query_table(table_name, conditions=None, limit=100):
    try:
        payload = {
            'sql': f"SELECT * FROM {table_name}",
            'limit': limit
        }
        if conditions:
            payload['sql'] += f" WHERE {conditions}"
        resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('rows', [])
        logger.error(f"Query failed: {resp.status_code} {resp.text}")
        return []
    except Exception as e:
        logger.error(f"Query exception: {e}")
        return []

def get_stale_services():
    threshold_time = datetime.utcnow() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    threshold_iso = threshold_time.isoformat() + 'Z'
    
    rows = query_table(HEALTH_TABLE, f"last_heartbeat < '{threshold_iso}'")
    return rows

def check_write_service_health():
    try:
        resp = requests.post(f'{WRITE_SERVICE_URL}/health', json={}, timeout=5)
        return resp.status_code == 200, resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        return False, {'error': str(e)}

def check_supervisord_status():
    try:
        import xmlrpc.client
        supervisor = xmlrpc.client.ServerProxy('http://localhost:9001/RPC2')
        all_info = supervisor.supervisor.getAllProcessInfo()
        
        results = {}
        for proc in all_info:
            results[proc['name']] = {
                'state': proc['state'],
                'statename': proc['statename'],
                'start': datetime.fromtimestamp(proc['start']),
                'exit_status': proc.get('exitstatus')
            }
        return True, results
    except Exception as e:
        return False, {'error': str(e)}

def check_recent_audit_events():
    try:
        payload = {
            'sql': "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 50",
            'limit': 50
        }
        resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('rows', [])
        return []
    except Exception as e:
        logger.error(f"Audit log query failed: {e}")
        return []

def check_disk_space():
    try:
        import shutil
        usage = shutil.disk_usage('/')
        return {
            'total_gb': usage.total / (1024**3),
            'used_gb': usage.used / (1024**3),
            'free_gb': usage.free / (1024**3),
            'percent': (usage.used / usage.total) * 100
        }
    except Exception as e:
        return {'error': str(e)}

def check_network_connectivity():
    results = {}
    endpoints = [
        ('localhost', 8772),
        ('localhost', 8773)
    ]
    for host, port in endpoints:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            results[f'{host}:{port}'] = 'UP' if result == 0 else f'DOWN (code={result})'
        except Exception as e:
            results[f'{host}:{port}'] = f'ERROR: {e}'
    return results

def analyze_timestamp_pattern(stale_rows):
    if not stale_rows:
        return None
    
    timestamps = []
    for row in stale_rows:
        ts = row.get('last_heartbeat', '')
        if ts:
            try:
                if 'Z' in ts:
                    ts = ts.replace('Z', '')
                dt = datetime.fromisoformat(ts)
                timestamps.append(dt)
            except:
                pass
    
    if len(timestamps) >= 2:
        min_ts = min(timestamps)
        max_ts = max(timestamps)
        spread = (max_ts - min_ts).total_seconds()
        return {
            'first_stale': min_ts.isoformat(),
            'last_stale': max_ts.isoformat(),
            'spread_seconds': spread
        }
    return None

def diagnose():
    logger.info("=== ZO-SENTINEL Stale Daemon Cluster Diagnostic ===")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}Z")
    print()
    
    print("=" * 60)
    print("1. CHECKING STALE SERVICES")
    print("=" * 60)
    stale = get_stale_services()
    print(f"Found {len(stale)} stale services (threshold: {STALE_THRESHOLD_MINUTES}min)")
    
    stale_by_service = defaultdict(list)
    for s in stale:
        svc = s.get('service', 'unknown')
        stale_by_service[svc].append(s)
    
    for svc in DAEMONS:
        if svc in stale_by_service:
            print(f"  [STALE] {svc}")
        else:
            print(f"  [OK]    {svc}")
    
    pattern = analyze_timestamp_pattern(stale)
    if pattern:
        print(f"\n  Stale timestamp pattern:")
        print(f"    First: {pattern['first_stale']}")
        print(f"    Last:  {pattern['last_stale']}")
        print(f"    Spread: {pattern['spread_seconds']:.0f} seconds")
    print()
    
    print("=" * 60)
    print("2. CHECKING WRITE_SERVICE HEALTH")
    print("=" * 60)
    ws_healthy, ws_status = check_write_service_health()
    if ws_healthy:
        print("  [OK] write_service responding")
    else:
        print(f"  [FAIL] write_service not responding: {ws_status}")
    print()
    
    print("=" * 60)
    print("3. CHECKING SUPERVISORD")
    print("=" * 60)
    sup_ok, sup_status = check_supervisord_status()
    if sup_ok:
        print("  Supervisord is accessible")
        for name, info in sup_status.items():
            if name in DAEMONS or 'write' in name:
                print(f"    {name}: {info['statename']} (started: {info['start']})")
    else:
        print(f"  [WARN] Cannot connect to supervisord: {sup_status}")
    print()
    
    print("=" * 60)
    print("4. CHECKING NETWORK CONNECTIVITY")
    print("=" * 60)
    network = check_network_connectivity()
    for endpoint, status in network.items():
        print(f"  {endpoint}: {status}")
    print()
    
    print("=" * 60)
    print("5. CHECKING DISK SPACE")
    print("=" * 60)
    disk = check_disk_space()
    if 'error' in disk:
        print(f"  [ERROR] {disk['error']}")
    else:
        print(f"  Total: {disk['total_gb']:.1f} GB")
        print(f"  Used:  {disk['used_gb']:.1f} GB ({disk['percent']:.1f}%)")
        print(f"  Free:  {disk['free_gb']:.1f} GB")
        if disk['percent'] > 90:
            print("  [WARN] Disk usage > 90%")
    print()
    
    print("=" * 60)
    print("6. ROOT CAUSE ANALYSIS")
    print("=" * 60)
    
    root_cause = None
    confidence = 0
    
    if not ws_healthy:
        root_cause = "write_service_failure"
        confidence = 90
        print("  [HIGH CONFIDENCE] write_service is not responding")
        print("  This would cause all heartbeat writes to fail, making daemons appear stale")
        print("  RECOMMENDATION: Restart write_service via 'supervisorctl restart write_service'")
    elif pattern and pattern['spread_seconds'] < 300:
        if sup_ok:
            for name, info in sup_status.items():
                if name == 'write_service' and info.get('start'):
                    if (datetime.utcnow() - info['start']).total_seconds() < 600:
                        root_cause = "supervisord_restart"
                        confidence = 85
                        print("  [HIGH CONFIDENCE] supervisord appears to have restarted recently")
                        print("  Daemons may not have re-registered their heartbeats properly")
                        print("  RECOMMENDATION: Verify all services have called heartbeat recently")
    
    if not root_cause:
        if len(stale) >= len(DAEMONS):
            root_cause = "cluster_wide_failure"
            confidence = 70
            print("  [MEDIUM CONFIDENCE] Cluster-wide failure detected")
            print("  Multiple services going stale simultaneously suggests:")
            print("    - Shared dependency failure (database, network)")
            print("    - System-level issue (OOM, kernel panic)")
            print("  RECOMMENDATION: Check system logs and shared service health")
        else:
            root_cause = "partial_failure"
            confidence = 50
            print("  [LOW CONFIDENCE] Partial failure pattern")
            print("  Some services stale, others OK - investigate individual service logs")
    
    print()
    print("=" * 60)
    print(f"DIAGNOSIS: {root_cause.upper()}")
    print(f"CONFIDENCE: {confidence}%")
    print("=" * 60)
    
    return root_cause, confidence

if __name__ == '__main__':
    cause, conf = diagnose()
    sys.exit(0 if cause else 1)