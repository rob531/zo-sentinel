import logging
import time
import requests
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('verify_tool_description_enrichment_effectiveness')

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'

SIGNAL_THRESHOLD = 10


def write_evidence(service_name, signal, distinct_count, status, details):
    payload = {
        'table': 'enrichment_evidence',
        'rows': {
            'service': service_name,
            'signal_type': signal,
            'distinct_score_count': distinct_count,
            'status': status,
            'details': details,
            'timestamp': datetime.utcnow().isoformat()
        },
        'wait': True
    }
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        response.raise_for_status()
        logger.info(f"Evidence written: {status} - {details}")
        return True
    except Exception as e:
        logger.error(f"Failed to write evidence: {e}")
        return False


def count_distinct_tool_description_scores():
    from src.mcp_sentinel_db import get_db_path
    import sqlite3
    
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT COUNT(DISTINCT score_value) 
            FROM mcp_signal_scores 
            WHERE signal_type = 'tool_description_safety'
        """)
        result = cursor.fetchone()
        distinct_count = result[0] if result else 0
        
        cursor.execute("""
            SELECT COUNT(*) FROM mcp_signal_scores 
            WHERE signal_type = 'tool_description_safety'
        """)
        total_records = cursor.fetchone()[0]
        
        return distinct_count, total_records
        
    finally:
        conn.close()


def run():
    logger.info("Starting tool_description enrichment effectiveness verification")
    
    distinct_count, total_records = count_distinct_tool_description_scores()
    
    logger.info(f"Distinct score values: {distinct_count} (total records: {total_records})")
    
    if distinct_count < SIGNAL_THRESHOLD:
        status = 'weak_signal'
        details = f"Only {distinct_count} distinct values found. Minimum threshold is {SIGNAL_THRESHOLD}. Tool description enrichment may need improvement."
        logger.warning(f"WEAK SIGNAL: {details}")
    else:
        status = 'adequate_signal'
        details = f"Found {distinct_count} distinct score values. Signal discrimination is adequate."
        logger.info(f"ADEQUATE SIGNAL: {details}")
    
    write_evidence(
        service_name='zo_sentinel_verify',
        signal='tool_description_safety',
        distinct_count=distinct_count,
        status=status,
        details=details
    )
    
    return distinct_count >= SIGNAL_THRESHOLD


if __name__ == '__main__':
    run()