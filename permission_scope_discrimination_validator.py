import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/permission_scope_discrimination_validator.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'permission_scope_discrimination_validator'
WRITE_SERVICE_URL = 'http://localhost:8772'

MIN_DISTINCT_SCORES_PRODUCTION = 5
MIN_DISTINCT_SCORES_ASPIRATIONAL = 20
NUM_SYNTHETIC_FINGERPRINTS = 34


def ws_query(sql: str) -> list:
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table: str, rows: list) -> None:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()


def validate_permission_scope_discrimination() -> tuple[bool, int, int]:
    sql = """
        SELECT DISTINCT score
        FROM mcp_signal_enrichments
        WHERE signal_name = 'permission_scope'
        ORDER BY score
    """
    rows = ws_query(sql)
    
    distinct_scores = [r.get('score') for r in rows if r.get('score') is not None]
    distinct_count = len(distinct_scores)
    
    logger.info(f"Permission scope signal distinct scores: {distinct_count}")
    logger.info(f"Score values found: {distinct_scores}")
    
    meets_production_threshold = distinct_count >= MIN_DISTINCT_SCORES_PRODUCTION
    meets_aspirational_threshold = distinct_count >= MIN_DISTINCT_SCORES_ASPIRATIONAL
    
    return meets_production_threshold, distinct_count, MIN_DISTINCT_SCORES_PRODUCTION


def main() -> None:
    logger.info("Starting permission_scope discrimination validation")
    logger.info(f"Checking mcp_signal_enrichments for signal_name='permission_scope'")
    logger.info(f"Production threshold: >={MIN_DISTINCT_SCORES_PRODUCTION} distinct scores")
    logger.info(f"Aspirational target: >={MIN_DISTINCT_SCORES_ASPIRATIONAL} distinct scores")
    
    try:
        meets_prod, count, threshold = validate_permission_scope_discrimination()
        
        validation_result = {
            'validator': SERVICE_NAME,
            'validated_at': datetime.now(timezone.utc).isoformat(),
            'distinct_score_count': count,
            'production_threshold': threshold,
            'aspirational_target': MIN_DISTINCT_SCORES_ASPIRATIONAL,
            'meets_production': meets_prod,
            'meets_aspirational': count >= MIN_DISTINCT_SCORES_ASPIRATIONAL
        }
        
        ws_write('validator_runs', [validation_result])
        
        if meets_prod:
            logger.info(f"PASS: Found {count} distinct scores (>= {threshold} required for production)")
            logger.info(f"Aspirational check: {'PASS' if count >= MIN_DISTINCT_SCORES_ASPIRATIONAL else 'BELOW TARGET'}")
            print(f"VALIDATION_RESULT=pass distinct_scores={count}")
            sys.exit(0)
        else:
            logger.warning(f"FAIL: Found {count} distinct scores (< {threshold} required for production)")
            logger.warning(f"permission_scope_enrichment_v2.py needs improvement to produce more granular scoring")
            print(f"VALIDATION_RESULT=fail distinct_scores={count} reason=below_production_threshold")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Validation error: {e}")
        raise


if __name__ == '__main__':
    main()