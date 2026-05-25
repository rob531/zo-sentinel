import logging
import requests
from datetime import datetime, timezone
import sys

SERVICE_NAME = 'tool_description_safety_discrimination_validator'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def ws_query(sql):
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status='running', meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    row = {'service_name': SERVICE_NAME, 'status': status, 'ts': ts, 'meta': meta or {}}
    ws_write('service_health', [row])


def validate_discrimination():
    # Count distinct scores for tool_description_safety signal
    sql_distinct = """
    SELECT COUNT(DISTINCT score) as distinct_scores, COUNT(*) as total_rows
    FROM mcp_signal_enrichments
    WHERE signal_name = 'tool_description_safety'
    """
    
    rows = ws_query(sql_distinct)
    if not rows:
        logger.error("No data returned for tool_description_safety signal")
        sys.exit(1)
    
    result = rows[0]
    distinct_scores = result.get('distinct_scores', 0)
    total_rows = result.get('total_rows', 0)
    
    logger.info(f"tool_description_safety: {distinct_scores} distinct scores across {total_rows} rows")
    
    # Check metadata field usage
    sql_meta_fields = """
    SELECT 
        COUNT(DISTINCT registry_source) as registry_sources,
        COUNT(DISTINCT tool_name) as tool_names,
        COUNT(DISTINCT metadata_hash) as metadata_hashes
    FROM mcp_signal_enrichments
    WHERE signal_name = 'tool_description_safety'
    """
    
    meta_rows = ws_query(sql_meta_fields)
    meta_result = meta_rows[0] if meta_rows else {}
    
    logger.info(f"Metadata usage: registry_sources={meta_result.get('registry_sources',0)}, tool_names={meta_result.get('tool_names',0)}, metadata_hashes={meta_result.get('metadata_hashes',0)}")
    
    # Get sample of score distribution
    sql_dist = """
    SELECT score, COUNT(*) as cnt
    FROM mcp_signal_enrichments
    WHERE signal_name = 'tool_description_safety'
    GROUP BY score
    ORDER BY score
    LIMIT 50
    """
    
    dist_rows = ws_query(sql_dist)
    logger.info(f"Score distribution ({len(dist_rows)} buckets):")
    for r in dist_rows:
        logger.info(f"  score={r['score']} count={r['cnt']}")
    
    # Pass/fail
    passed = distinct_scores > 20
    
    report = {
        'signal_name': 'tool_description_safety',
        'distinct_scores': distinct_scores,
        'total_rows': total_rows,
        'registry_sources': meta_result.get('registry_sources', 0),
        'tool_names': meta_result.get('tool_names', 0),
        'metadata_hashes': meta_result.get('metadata_hashes', 0),
        'threshold': 20,
        'passed': passed,
        'ts': datetime.now(timezone.utc).isoformat()
    }
    
    ws_write('tool_description_safety_discrimination_validation', [report])
    
    logger.info(f"VALIDATION RESULT: {'PASS' if passed else 'FAIL'} - {distinct_scores} distinct scores (threshold >20)")
    
    send_heartbeat('completed' if passed else 'failed', report)
    
    if not passed:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    validate_discrimination()