import requests
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/tool_description_safety_enrichment_wiring.log')]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'tool_description_safety_enrichment_wiring'
PORT = None
PID_FILE = '/tmp/tool_description_safety_enrichment_wiring.pid'

from tool_description_safety_enrichment import compute_score


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql):
    payload = {'sql': sql}
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    import os, sys
    if os.path.exists(PID_FILE):
        old_pid = open(PID_FILE).read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    import sys
    sys.exit(0)


def send_heartbeat():
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    row = {'service': SERVICE_NAME, 'last_heartbeat': ts, 'status': 'running', 'meta': '{}'}
    ws_write('service_health', [row])


def cycle():
    sql = """
    SELECT server_id, name, description, metadata
    FROM mcp_server_registry
    WHERE metadata IS NOT NULL
      AND metadata != ''
      AND server_id NOT IN (
          SELECT server_id FROM mcp_signal_enrichments
          WHERE signal_type = 'tool_description_safety_enrichment'
      )
    LIMIT 50
    """
    result = ws_query(sql)
    rows = result.get('rows', [])
    if not rows:
        logger.info("No pending servers for tool_description_safety_enrichment")
        return

    logger.info(f"Processing {len(rows)} servers")
    for row in rows:
        server_id = row.get('server_id')
        name = row.get('name', '')
        description = row.get('description', '')
        metadata_str = row.get('metadata', '')

        try:
            import json
            metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        try:
            score_result = compute_score(metadata)
            score_value = score_result.score if hasattr(score_result, 'score') else float(score_result)
            reasons = score_result.reasons if hasattr(score_result, 'reasons') else []
            flags = score_result.flags if hasattr(score_result, 'flags') else []
            from datetime import datetime, timezone
            computed_at = datetime.now(timezone.utc).isoformat()
            enrich_row = {
                'server_id': server_id,
                'signal_type': 'tool_description_safety_enrichment',
                'score': score_value,
                'evidence': json.dumps({'reasons': reasons, 'flags': flags}),
                'computed_at': computed_at,
                'tool_count': metadata.get('tool_count', 0),
                'dangerous_patterns': json.dumps(flags)
            }
            ws_write('mcp_signal_enrichments', [enrich_row])
            logger.info(f"Processed server_id={server_id} score={score_value}")
        except Exception as e:
            logger.error(f"Error processing server_id={server_id}: {e}")


def run():
    import signal, time
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info(f"Starting {SERVICE_NAME}")
    POLL_SECS = 60
    while True:
        try:
            send_heartbeat()
            cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}")
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()