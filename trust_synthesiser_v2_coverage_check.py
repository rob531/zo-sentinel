import requests
import json
import os
import re
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/coverage_check.log')]
)
logger = logging.getLogger('coverage_check')

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"

def ws_query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()

def read_source_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def extract_weight_from_code(code, dimension):
    pattern = rf'{dimension}[_\s]*weight["\']?\s*[:=]\s*([0-9.]+)'
    match = re.search(pattern, code, re.IGNORECASE)
    if match:
        return float(match.group(1))
    weight_patterns = [
        rf'weight["\']?\s*[:=]\s*([0-9.]+).*?{dimension}',
        rf'WEIGHTS\s*=\s*\{{[^}}]*{dimension}[^}}]*?weight["\']?\s*[:=]\s*([0-9.]+)',
        rf'"{dimension}".*?weight["\']?\s*[:=]\s*([0-9.]+)',
    ]
    for pattern in weight_patterns:
        match = re.search(pattern, code, re.DOTALL | re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

def extract_threshold_from_code(code, dimension):
    pattern = rf'{dimension}[_\s]*threshold["\']?\s*[:=]\s*([0-9.]+)'
    match = re.search(pattern, code, re.IGNORECASE)
    if match:
        return float(match.group(1))
    threshold_patterns = [
        rf'threshold["\']?\s*[:=]\s*([0-9.]+).*?{dimension}',
        rf'THRESHOLDS\s*=\s*\{{[^}}]*{dimension}[^}}]*?threshold["\']?\s*[:=]\s*([0-9.]+)',
    ]
    for pattern in threshold_patterns:
        match = re.search(pattern, code, re.DOTALL | re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

def check_code_reads_dimension(code, dimension):
    patterns = [
        rf'["\']\s*{dimension}\s*["\']',
        rf'dimension\s*==\s*["\']{dimension}["\']',
        rf'["\']{dimension}["\'].*?signal',
        rf'signal.*?["\']{dimension}["\']',
        rf'injection_resilience',
    ]
    for pattern in patterns:
        if re.search(pattern, code, re.IGNORECASE):
            return True
    return False

def check_dimension_in_db(dimension):
    sql = """
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'mcp_signal_scores' 
    AND column_name = 'dimension'
    LIMIT 1
    """
    try:
        result = ws_query(sql)
        if not result or len(result) == 0:
            logger.warning("dimension column not found in mcp_signal_scores")
            return False
    except Exception as e:
        logger.warning(f"Could not query information_schema: {e}")
    
    sql = f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE dimension = '{dimension}'"
    try:
        result = ws_query(sql)
        if result and len(result) > 0:
            cnt = result[0].get('cnt', 0) if isinstance(result[0], dict) else result[0][0] if result[0] else 0
            logger.info(f"Found {cnt} rows for dimension '{dimension}' in mcp_signal_scores")
            return cnt > 0
    except Exception as e:
        logger.warning(f"Could not query mcp_signal_scores: {e}")
    
    sql = "SELECT dimension FROM mcp_signal_scores GROUP BY dimension LIMIT 20"
    try:
        result = ws_query(sql)
        logger.info(f"Available dimensions: {result}")
        dimensions = [r.get('dimension', r[0] if isinstance(r, (list, tuple)) else r) for r in result]
        return dimension in dimensions
    except Exception as e:
        logger.warning(f"Could not list dimensions: {e}")
    
    return False

def check_signal_enrichments_coverage(code):
    table_refs = [
        r'mcp_signal_enrichments',
        r'mcp_signal_scores',
    ]
    found = set()
    for ref in table_refs:
        if ref in code.lower():
            found.add(ref)
    return found

def main():
    logger.info("=" * 60)
    logger.info("TRUST_SYNTHESISER_V2 SIGNAL COVERAGE CHECK")
    logger.info(f"Started: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    source_path = "/home/workspace/zo_sentinel/trust_synthesiser_v2.py"
    
    checks = {
        "source_exists": False,
        "dimension_in_db": False,
        "code_reads_dimension": False,
        "weight_correct": False,
        "threshold_correct": False,
        "signal_tables_referenced": set(),
    }
    
    if os.path.exists(source_path):
        checks["source_exists"] = True
        logger.info(f"Source file found: {source_path}")
        code = read_source_file(source_path)
        
        checks["code_reads_dimension"] = check_code_reads_dimension(code, "injection_resilience")
        logger.info(f"Code reads 'injection_resilience': {checks['code_reads_dimension']}")
        
        checks["weight_correct"] = extract_weight_from_code(code, "injection_resilience") == 1.6
        weight_val = extract_weight_from_code(code, "injection_resilience")
        logger.info(f"Weight for injection_resilience: {weight_val} (expected 1.6)")
        
        checks["threshold_correct"] = extract_threshold_from_code(code, "injection_resilience") == 0.80
        threshold_val = extract_threshold_from_code(code, "injection_resilience")
        logger.info(f"Threshold for injection_resilience: {threshold_val} (expected 0.80)")
        
        checks["signal_tables_referenced"] = check_signal_enrichments_coverage(code)
        logger.info(f"Signal tables referenced: {checks['signal_tables_referenced']}")
    else:
        logger.error(f"Source file not found: {source_path}")
    
    checks["dimension_in_db"] = check_dimension_in_db("injection_resilience")
    logger.info(f"Dimension 'injection_resilience' in DB: {checks['dimension_in_db']}")
    
    logger.info("-" * 60)
    logger.info("CHECK RESULTS:")
    logger.info(f"  Source exists:          {checks['source_exists']}")
    logger.info(f"  Dimension in DB:        {checks['dimension_in_db']}")
    logger.info(f"  Code reads dimension:   {checks['code_reads_dimension']}")
    logger.info(f"  Weight 1.6 correct:     {checks['weight_correct']}")
    logger.info(f"  Threshold 0.80 correct: {checks['threshold_correct']}")
    logger.info(f"  Signal tables found:    {len(checks['signal_tables_referenced'])} (mcp_signal_scores, mcp_signal_enrichments)")
    
    all_passed = (
        checks["source_exists"] and
        checks["dimension_in_db"] and
        checks["code_reads_dimension"] and
        checks["weight_correct"] and
        checks["threshold_correct"] and
        len(checks["signal_tables_referenced"]) >= 1
    )
    
    logger.info("-" * 60)
    if all_passed:
        logger.info("OVERALL: PASS - All coverage checks passed")
    else:
        logger.info("OVERALL: FAIL - Some coverage checks failed")
    
    logger.info(f"Completed: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    import sys
    sys.exit(0 if all_passed else 1)

if __name__ == "__main__":
    main()