import os
import logging
import requests
from datetime import datetime, timezone

# Module-level logger - no basicConfig in library module
logger = logging.getLogger(__name__)

# Constants
SERVICE_NAME = 'anti_entropy_diagnosis'
WRITE_SERVICE_URL = 'http://localhost:8772'
ZO_SENTINEL_DIR = '/home/workspace/zo_sentinel'
LOG_DIR = '/home/workspace/logs'

# Write helper via HTTP
def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()

# Query helper via HTTP
def ws_query(sql):
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()

# Heartbeat helper
def send_heartbeat(status, meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': meta or {}
    }])

def diagnose():
    """Diagnose anti_entropy and wisdom_synthesiser daemons."""
    results = []
    
    # Daemons to check (name, file, threshold_seconds)
    daemons = [
        ('anti_entropy', 'anti_entropy.py', 14400),
        ('wisdom_synthesiser', 'wisdom_synthesiser.py', 14400),
    ]
    
    for daemon_name, daemon_file, staleness_threshold in daemons:
        logger.info(f"=== Diagnosing {daemon_name} ===")
        
        # 1. Query service_health for this daemon
        try:
            health_result = ws_query(f"""
                SELECT service_name, status, last_heartbeat, meta
                FROM service_health
                WHERE service_name = '{daemon_name}'
                ORDER BY last_heartbeat DESC
                LIMIT 1
            """)
            
            if health_result and len(health_result) > 0:
                row = health_result[0]
                last_heartbeat_str = row.get('last_heartbeat', '')
                
                # Calculate staleness
                if last_heartbeat_str:
                    try:
                        last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
                        now = datetime.now(timezone.utc)
                        age_seconds = (now - last_heartbeat).total_seconds()
                        stale = age_seconds > staleness_threshold
                        
                        logger.info(f"  service_health: status={row.get('status')}, last_heartbeat={last_heartbeat_str}")
                        logger.info(f"  staleness: {age_seconds:.0f}s (threshold={staleness_threshold}s, stale={stale})")
                        
                        results.append({
                            'daemon': daemon_name,
                            'file_exists': False,
                            'last_heartbeat': last_heartbeat_str,
                            'stale_seconds': age_seconds,
                            'is_stale': stale,
                            'health_status': row.get('status'),
                            'errors': []
                        })
                    except Exception as e:
                        logger.error(f"  Error parsing heartbeat: {e}")
                        results.append({
                            'daemon': daemon_name,
                            'file_exists': False,
                            'last_heartbeat': last_heartbeat_str,
                            'stale_seconds': None,
                            'is_stale': None,
                            'health_status': row.get('status'),
                            'errors': [str(e)]
                        })
                else:
                    logger.warning(f"  No last_heartbeat in service_health")
            else:
                logger.warning(f"  No service_health entry found for {daemon_name}")
                results.append({
                    'daemon': daemon_name,
                    'file_exists': False,
                    'last_heartbeat': None,
                    'stale_seconds': None,
                    'is_stale': True,  # No heartbeat = definitely stale
                    'health_status': None,
                    'errors': ['No service_health entry']
                })
        except Exception as e:
            logger.error(f"  Error querying service_health: {e}")
            results.append({
                'daemon': daemon_name,
                'file_exists': False,
                'last_heartbeat': None,
                'stale_seconds': None,
                'is_stale': True,
                'health_status': None,
                'errors': [str(e)]
            })
        
        # 2. Check if daemon file exists on disk
        daemon_path = os.path.join(ZO_SENTINEL_DIR, daemon_file)
        file_exists = os.path.isfile(daemon_path)
        logger.info(f"  file_exists: {file_exists} ({daemon_path})")
        
        if results:
            results[-1]['file_exists'] = file_exists
        
        # 3. Inspect recent errors in log file
        log_file = os.path.join(LOG_DIR, f'{daemon_name}.log')
        recent_errors = []
        
        if os.path.isfile(log_file):
            try:
                # Read last 100 lines, look for ERROR/Exception
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    error_lines = [l.strip() for l in lines[-100:] if 'ERROR' in l or 'Exception' in l]
                    recent_errors = error_lines[-5:]  # Last 5 errors
                    logger.info(f"  log_errors: {len(recent_errors)} recent errors found")
                    for err in recent_errors:
                        logger.info(f"    {err}")
            except Exception as e:
                logger.error(f"  Error reading log file: {e}")
                recent_errors = [f'Log read error: {e}']
        else:
            logger.warning(f"  No log file found at {log_file}")
        
        if results:
            results[-1]['errors'] = results[-1].get('errors', []) + recent_errors
    
    # Summary
    logger.info("=== DIAGNOSIS SUMMARY ===")
    for r in results:
        logger.info(f"  {r['daemon']}: stale={r.get('is_stale')}, file_exists={r.get('file_exists')}, errors={len(r.get('errors', []))}")
    
    # Write results to diagnostics table
    try:
        ws_write('anti_entropy_diagnosis_results', [{
            'daemon': r['daemon'],
            'file_exists': r.get('file_exists', False),
            'last_heartbeat': r.get('last_heartbeat'),
            'stale_seconds': r.get('stale_seconds'),
            'is_stale': r.get('is_stale'),
            'health_status': r.get('health_status'),
            'error_count': len(r.get('errors', [])),
            'errors_json': str(r.get('errors', [])),
            'diagnosed_at': datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        logger.error(f"Failed to write diagnosis results: {e}")
    
    return results

if __name__ == '__main__':
    # Configure logging for entry point
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.FileHandler(os.path.join(LOG_DIR, f'{SERVICE_NAME}.log'))]
    )
    
    logger.info(f"Starting {SERVICE_NAME}")
    
    try:
        diagnose()
        send_heartbeat('completed', {'daemons_checked': 2})
        logger.info("Diagnosis complete")
        import sys
        sys.exit(0)
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}")
        send_heartbeat('failed', {'error': str(e)})
        import sys
        sys.exit(1)