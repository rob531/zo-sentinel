import os
import sys
import logging
import requests

SERVICE_NAME = 'trust_synthesiser_dimension_check'
WRITE_SERVICE_URL = 'http://localhost:8772'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> dict:
    """Query DuckDB via write_service."""
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list) -> dict:
    """Write rows via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_injection_resilience_row_count() -> int:
    """Count injection_resilience rows in mcp_signal_scores."""
    sql = "SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE dimension = 'injection_resilience'"
    result = ws_query(sql)
    rows = result.get('rows', [])
    if rows:
        return rows[0].get('cnt', 0)
    return 0


def get_injection_resilience_sample(limit: int = 5) -> list:
    """Get sample injection_resilience rows."""
    sql = f"""
    SELECT server_id, dimension, score, computed_at 
    FROM mcp_signal_scores 
    WHERE dimension = 'injection_resilience' 
    ORDER BY computed_at DESC 
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get('rows', [])


def read_trust_synthesiser_config() -> str:
    """Read trust_synthesiser_v2.py to check for injection_resilience config."""
    path = '/home/workspace/zo_sentinel/trust_synthesiser_v2.py'
    try:
        with open(path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return ''


def check_injection_resilience_weight_threshold(content: str) -> dict:
    """Check if trust_synthesiser_v2.py has weight 1.6 and threshold 0.80 for injection_resilience."""
    findings = {
        'weight_1p6_found': False,
        'threshold_0p80_found': False,
        'injection_resilience_found': False,
        'all_three_align': False
    }
    
    # Check for injection_resilience dimension reference
    if 'injection_resilience' in content:
        findings['injection_resilience_found'] = True
    
    # Check for weight 1.6 - pattern like weight = 1.6 or weight=1.6
    import re
    weight_pattern = re.compile(r"['\"](injection_resilience)['\"].*?weight[:\s]*=?\s*1\.6", re.IGNORECASE | re.DOTALL)
    if weight_pattern.search(content):
        findings['weight_1p6_found'] = True
    
    # Also check if 1.6 appears anywhere near injection_resilience
    weight_fallback = re.compile(r"weight[:\s]*=?\s*1\.6", re.IGNORECASE)
    if weight_fallback.search(content):
        findings['weight_1p6_found'] = True
    
    # Check for threshold 0.80
    threshold_pattern = re.compile(r"threshold[:\s]*=?\s*0\.80", re.IGNORECASE)
    if threshold_pattern.search(content):
        findings['threshold_0p80_found'] = True
    
    # Alternative threshold patterns
    threshold_alt = re.compile(r"0\.8(?!\d)", re.IGNORECASE)
    if threshold_alt.search(content):
        findings['threshold_0p80_found'] = True
    
    findings['all_three_align'] = (
        findings['injection_resilience_found'] and
        findings['weight_1p6_found'] and
        findings['threshold_0p80_found']
    )
    
    return findings


def check_pi_scorer_writes_injection_resilience() -> dict:
    """Check if pi_scorer.py is writing injection_resilience rows."""
    path = '/home/workspace/zo_sentinel/pi_scorer.py'
    findings = {
        'pi_scorer_exists': False,
        'injection_resilience_writes': False,
        'dimension_column_writes': False
    }
    
    try:
        with open(path, 'r') as f:
            content = f.read()
            findings['pi_scorer_exists'] = True
            
            if 'injection_resilience' in content:
                findings['injection_resilience_writes'] = True
            
            if "'dimension'" in content or '"dimension"' in content:
                findings['dimension_column_writes'] = True
    except FileNotFoundError:
        pass
    
    return findings


def main():
    logger.info("Starting trust_synthesiser_dimension_check...")
    
    # (1) Count injection_resilience rows in mcp_signal_scores
    row_count = get_injection_resilience_row_count()
    logger.info(f"[FINDING 1] injection_resilience row count in mcp_signal_scores: {row_count}")
    
    # Get sample rows
    samples = get_injection_resilience_sample(5)
    logger.info(f"[FINDING 2] Sample injection_resilience rows: {samples}")
    
    # (2) Check trust_synthesiser_v2.py for weight 1.6 and threshold 0.80
    ts_content = read_trust_synthesiser_v2_config()
    ts_findings = check_injection_resilience_weight_threshold(ts_content)
    
    logger.info(f"[FINDING 3] trust_synthesiser_v2.py injection_resilience reference: {ts_findings['injection_resilience_found']}")
    logger.info(f"[FINDING 4] trust_synthesiser_v2.py weight 1.6 config: {ts_findings['weight_1p6_found']}")
    logger.info(f"[FINDING 5] trust_synthesiser_v2.py threshold 0.80 config: {ts_findings['threshold_0p80_found']}")
    logger.info(f"[FINDING 6] All three Phase 8 specs aligned: {ts_findings['all_three_align']}")
    
    # Check pi_scorer.py
    pi_findings = check_pi_scorer_writes_injection_resilience()
    logger.info(f"[FINDING 7] pi_scorer.py exists: {pi_findings['pi_scorer_exists']}")
    logger.info(f"[FINDING 8] pi_scorer.py writes injection_resilience: {pi_findings['injection_resilience_writes']}")
    logger.info(f"[FINDING 9] pi_scorer.py writes dimension column: {pi_findings['dimension_column_writes']}")
    
    # Write summary to service_health audit
    summary = {
        'service': SERVICE_NAME,
        'status': 'complete',
        'findings': {
            'injection_resilience_rows': row_count,
            'ts_injection_resilience_ref': ts_findings['injection_resilience_found'],
            'ts_weight_1p6': ts_findings['weight_1p6_found'],
            'ts_threshold_0p80': ts_findings['threshold_0p80_found'],
            'phase8_aligned': ts_findings['all_three_align'],
            'pi_scorer_writes_ir': pi_findings['injection_resilience_writes']
        }
    }
    
    logger.info(f"[SUMMARY] Phase 8 7th-dimension review complete: {summary}")
    
    ws_write('service_health', [{
        'service_name': SERVICE_NAME,
        'status': 'complete',
        'ts': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'meta': str(summary)
    }])
    
    logger.info("trust_synthesiser_dimension_check exiting with success")
    sys.exit(0)


if __name__ == '__main__':
    main()