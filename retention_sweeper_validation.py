import logging
import os
import sys
import signal
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/retention_sweeper_validation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'retention_sweeper_validation'
PORT = None
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
RETENTION_DAYS = 30
POLL_SECS = 3600


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f'{SERVICE_NAME} already running as PID {old_pid}')
            sys.exit(1)
        except OSError:
            logger.warning(f'Stale PID file found for PID {old_pid}, removing')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    logger.info(f'{SERVICE_NAME} started with PID {os.getpid()}')


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def ws_query(sql):
    try:
        import requests
        response = requests.post(
            QUERY_URL,
            json={'sql': sql},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f'ws_query failed: {e}')
        return {'rows': [], 'error': str(e)}


def ws_write(table, rows):
    try:
        import requests
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f'ws_write failed for table {table}: {e}')
        return {'error': str(e)}


def send_heartbeat(status='running', meta=None):
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [row])


def get_expired_signal_scores_cutoff():
    return (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()


def get_expired_enrichments_cutoff():
    return (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()


def query_expired_signal_scores():
    cutoff = get_expired_signal_scores_cutoff()
    sql = f"""
    SELECT 
        server_id,
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE scored_at < '{cutoff}'
    ORDER BY scored_at ASC
    LIMIT 1000
    """
    result = ws_query(sql)
    return result.get('rows', [])


def query_expired_signal_enrichments():
    cutoff = get_expired_enrichments_cutoff()
    sql = f"""
    SELECT 
        server_id,
        signal_type,
        computed_at,
        evidence
    FROM mcp_signal_enrichments
    WHERE computed_at < '{cutoff}'
    ORDER BY computed_at ASC
    LIMIT 1000
    """
    result = ws_query(sql)
    return result.get('rows', [])


def count_expired_signal_scores():
    cutoff = get_expired_signal_scores_cutoff()
    sql = f"""
    SELECT COUNT(*) as expired_count
    FROM mcp_signal_scores
    WHERE scored_at < '{cutoff}'
    """
    result = ws_query(sql)
    if result.get('rows'):
        return result['rows'][0].get('expired_count', 0)
    return 0


def count_expired_signal_enrichments():
    cutoff = get_expired_enrichments_cutoff()
    sql = f"""
    SELECT COUNT(*) as expired_count
    FROM mcp_signal_enrichments
    WHERE computed_at < '{cutoff}'
    """
    result = ws_query(sql)
    if result.get('rows'):
        return result['rows'][0].get('expired_count', 0)
    return 0


def check_table_exists(table_name):
    sql = f"""
    SELECT COUNT(*) as cnt
    FROM information_schema.tables 
    WHERE table_name = '{table_name}'
    """
    result = ws_query(sql)
    if result.get('rows'):
        return result['rows'][0].get('cnt', 0) > 0
    return False


def generate_validation_report():
    report = {
        'generated_at': utc_now_iso(),
        'retention_days': RETENTION_DAYS,
        'cutoff_date': get_expired_signal_scores_cutoff(),
        'tables_checked': [],
        'total_expired': 0,
        'validation_status': 'PASS',
        'details': []
    }
    
    signal_scores_exists = check_table_exists('mcp_signal_scores')
    enrichments_exists = check_table_exists('mcp_signal_enrichments')
    
    if not signal_scores_exists:
        report['validation_status'] = 'FAIL'
        report['details'].append({
            'table': 'mcp_signal_scores',
            'status': 'MISSING',
            'message': 'Table does not exist in database'
        })
    else:
        expired_count = count_expired_signal_scores()
        expired_rows = query_expired_signal_scores()
        report['tables_checked'].append('mcp_signal_scores')
        report['total_expired'] += expired_count
        report['details'].append({
            'table': 'mcp_signal_scores',
            'status': 'OK',
            'expired_rows_found': expired_count,
            'sample_rows': expired_rows[:10],
            'message': f'Found {expired_count} rows older than {RETENTION_DAYS} days that would be flagged for cleanup'
        })
    
    if not enrichments_exists:
        report['validation_status'] = 'WARN'
        report['details'].append({
            'table': 'mcp_signal_enrichments',
            'status': 'MISSING',
            'message': 'Table does not exist in database'
        })
    else:
        expired_count = count_expired_signal_enrichments()
        expired_rows = query_expired_signal_enrichments()
        report['tables_checked'].append('mcp_signal_enrichments')
        report['total_expired'] += expired_count
        report['details'].append({
            'table': 'mcp_signal_enrichments',
            'status': 'OK',
            'expired_rows_found': expired_count,
            'sample_rows': expired_rows[:10],
            'message': f'Found {expired_count} rows older than {RETENTION_DAYS} days that would be flagged for cleanup'
        })
    
    return report


def print_report(report):
    logger.info('=' * 70)
    logger.info('RETENTION SWEEPER VALIDATION REPORT')
    logger.info('=' * 70)
    logger.info(f"Generated at: {report['generated_at']}")
    logger.info(f"Retention policy: {report['retention_days']} days")
    logger.info(f"Cutoff date: {report['cutoff_date']}")
    logger.info(f"Validation status: {report['validation_status']}")
    logger.info(f"Total expired rows (would be cleaned): {report['total_expired']}")
    logger.info('-' * 70)
    
    for detail in report['details']:
        logger.info(f"Table: {detail.get('table', 'unknown')}")
        logger.info(f"  Status: {detail.get('status', 'unknown')}")
        logger.info(f"  Message: {detail.get('message', 'no message')}")
        if 'expired_rows_found' in detail:
            logger.info(f"  Expired rows: {detail['expired_rows_found']}")
            if detail.get('sample_rows'):
                logger.info(f"  Sample (first 3 rows):")
                for i, row in enumerate(detail['sample_rows'][:3]):
                    logger.info(f"    [{i+1}] server_id={row.get('server_id')}, scored_at={row.get('scored_at') or row.get('computed_at')}")
        logger.info('')
    
    logger.info('=' * 70)
    logger.info('NO DELETE OPERATIONS WERE EXECUTED')
    logger.info('This report shows what WOULD be expired, for validation only')
    logger.info('=' * 70)


def cycle():
    logger.info('Starting retention_sweeper validation cycle')
    report = generate_validation_report()
    print_report(report)
    return report


def run():
    logger.info(f'Starting {SERVICE_NAME} daemon')
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    while True:
        try:
            report = cycle()
            meta = {
                'validation_status': report['validation_status'],
                'total_expired': report['total_expired'],
                'tables_checked': report['tables_checked']
            }
            send_heartbeat(status='running', meta=meta)
        except Exception as e:
            logger.error(f'Error in cycle: {e}')
            send_heartbeat(status='error', meta={'error': str(e)})
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()