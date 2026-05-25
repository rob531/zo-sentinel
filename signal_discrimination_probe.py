import requests
import logging
from datetime import datetime, timezone
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/signal_discrimination_probe.log')]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'signal_discrimination_probe'

def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows):
    if isinstance(rows, dict):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat(status='running', meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': ts,
        'meta': meta or {}
    }
    ws_write('service_health', row)

def get_signal_cardinality(signal_type):
    sql = """
    SELECT COUNT(DISTINCT score_value) as cardinality,
           COUNT(*) as total_rows,
           MIN(score_value) as min_val,
           MAX(score_value) as max_val,
           ARRAY_AGG(DISTINCT score_value ORDER BY score_value) as unique_values
    FROM mcp_signal_scores
    WHERE signal_type = %s
    """
    result = ws_query(sql, (signal_type,))
    return result[0] if result else None

def get_metadata_variance(signal_type):
    sql = """
    SELECT 
        COUNT(DISTINCT metadata_score) as metadata_cardinality,
        COUNT(*) as total_rows,
        ARRAY_AGG(DISTINCT metadata_score ORDER BY metadata_score) as unique_metadata
    FROM mcp_signal_scores
    WHERE signal_type = %s
    """
    result = ws_query(sql, (signal_type,))
    return result[0] if result else None

def get_sample_records(signal_type, limit=10):
    sql = """
    SELECT signal_type, score_value, metadata_score, computed_at
    FROM mcp_signal_scores
    WHERE signal_type = %s
    ORDER BY computed_at DESC
    LIMIT %s
    """
    result = ws_query(sql, (signal_type, limit))
    return result

def investigate_signal(signal_type):
    logger.info(f"Investigating signal_type={signal_type}")
    
    cardinality = get_signal_cardinality(signal_type)
    metadata_variance = get_metadata_variance(signal_type)
    samples = get_sample_records(signal_type, 20)
    
    findings = {
        'signal_type': signal_type,
        'cardinality': cardinality,
        'metadata_variance': metadata_variance,
        'sample_count': len(samples),
        'samples': samples[:5]
    }
    
    logger.info(f"  cardinality={cardinality}")
    logger.info(f"  metadata_variance={metadata_variance}")
    
    return findings

def cycle():
    logger.info("Starting signal discrimination diagnostic")
    
    signal_types = ['permission_scope', 'temporal_stability', 'tool_description_safety']
    results = []
    
    for st in signal_types:
        findings = investigate_signal(st)
        results.append(findings)
        
        if findings['cardinality']:
            card = findings['cardinality']
            logger.info(f"  [{st}] cardinality={card['cardinality']}, unique_values={card.get('unique_values', 'N/A')}")
        
        if findings['metadata_variance']:
            mv = findings['metadata_variance']
            logger.info(f"  [{st}] metadata cardinality={mv['metadata_cardinality']}, unique={mv.get('unique_metadata', 'N/A')}")
    
    output = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'service': SERVICE_NAME,
        'signal_types_analyzed': signal_types,
        'results': results,
        'summary': {
            st: (r['cardinality']['cardinality'] if r.get('cardinality') else 'N/A')
            for st, r in zip(signal_types, results)
        }
    }
    
    ws_write('signal_diagnostic_results', output)
    
    logger.info("Diagnostic complete. Results written.")
    logger.info(f"Summary: {output['summary']}")
    
    return output

def run():
    logger.info(f"{SERVICE_NAME} starting")
    while True:
        try:
            cycle()
            send_heartbeat(status='idle', meta={'last_run': datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            logger.error(f"Error in cycle: {e}")
            send_heartbeat(status='error', meta={'error': str(e)})
        import time
        time.sleep(300)

if __name__ == '__main__':
    run()