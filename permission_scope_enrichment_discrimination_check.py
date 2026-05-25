import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'permission_scope_enrichment_discrimination_check'
WRITE_SERVICE_URL = 'http://localhost:8772'

import requests
from datetime import datetime, timezone


def ws_query(sql, params=None):
    payload = {'table': '__inline_query__', 'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    if isinstance(rows, dict):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status, meta=''):
    ws_write('service_health', {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': meta
    })


def get_distinct_permission_scope_scores():
    sql = """
    SELECT
        COUNT(DISTINCT score_value) AS distinct_count,
        MIN(score_value) AS min_score,
        MAX(score_value) AS max_score,
        COLLECT(DISTINCT score_value) AS all_values
    FROM mcp_signal_scores
    WHERE signal_name = 'permission_scope'
    """
    rows = ws_query(sql)
    if not rows:
        logger.error('No rows returned from mcp_signal_scores for permission_scope')
        return None
    return rows[0]


def inspect_v2_source_for_metadata_fields():
    """Read the v2 enrichment file and confirm it's reading multiple fields."""
    v2_path = '/home/workspace/zo_sentinel/permission_scope_enrichment_v2.py'
    try:
        with open(v2_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        logger.warning('v2 file not found at %s, skipping source inspection', v2_path)
        return None

    fields_found = []
    candidates = ['registry_source', 'age_days', 'download_count', 'dependencies_count',
                   'last_updated_days', 'author_count', 'homepage_exists']
    for field in candidates:
        if field in content:
            fields_found.append(field)

    logger.info('Fields referenced in v2 source: %s', fields_found)
    return fields_found


def run():
    logger.info('=== permission_scope discrimination check ===')
    send_heartbeat('running', 'discrimination_check_active')

    # Step 1: inspect v2 source
    fields = inspect_v2_source_for_metadata_fields()
    v2_ok = False
    if fields and len(fields) >= 2:
        logger.info('PASS: v2 reads %d metadata fields: %s', len(fields), fields)
        v2_ok = True
    else:
        logger.warning('FAIL: v2 reads only %d fields (need >= 2): %s',
                       len(fields) if fields else 0, fields)

    # Step 2: check distinct scores
    score_info = get_distinct_permission_scope_scores()
    if score_info is None:
        send_heartbeat('error', 'query_returned_no_rows')
        sys.exit(1)

    distinct_count = score_info.get('distinct_count', 0)
    all_values = score_info.get('all_values', [])
    min_score = score_info.get('min_score')
    max_score = score_info.get('max_score')

    logger.info('Distinct permission_scope scores: %d', distinct_count)
    logger.info('Score range: %s - %s', min_score, max_score)
    logger.info('All values: %s', sorted(all_values) if all_values else [])

    THRESHOLD = 4
    if distinct_count > THRESHOLD:
        logger.info('PASS: distinct_count=%d > %d', distinct_count, THRESHOLD)
        status = 'pass'
        meta = f'distinct={distinct_count} fields={len(fields) if fields else 0}'
    else:
        logger.error('FAIL: distinct_count=%d is NOT > %d (plateau detected)', distinct_count, THRESHOLD)
        status = 'fail'
        meta = f'distinct={distinct_count} fields={len(fields) if fields else 0} PLATEAU'

    send_heartbeat(status, meta)
    logger.info('=== check complete ===')
    if status == 'pass':
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    run()