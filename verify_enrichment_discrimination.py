import logging
import sys
import requests
from datetime import datetime, timezone

SERVICE_NAME = "verify_enrichment_discrimination"
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

MIN_DISCRIMINATION_THRESHOLD = 10

def ws_query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])

def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def check_enrichment_discrimination():
    logger.info("Starting enrichment discrimination verification")
    
    query = """
    SELECT 
        signal_type,
        enrichment_key,
        COUNT(DISTINCT enrichment_value) as distinct_count
    FROM mcp_signal_enrichments
    GROUP BY signal_type, enrichment_key
    ORDER BY signal_type, enrichment_key
    """
    
    results = ws_query(query)
    
    if not results:
        logger.warning("No enrichments found in mcp_signal_enrichments table")
        return False
    
    failing_enrichments = []
    passing_enrichments = []
    
    for row in results:
        signal_type = row.get("signal_type", "unknown")
        enrichment_key = row.get("enrichment_key", "unknown")
        distinct_count = row.get("distinct_count", 0)
        
        identifier = f"{signal_type}::{enrichment_key}"
        
        if distinct_count < MIN_DISCRIMINATION_THRESHOLD:
            logger.warning(
                f"FAIL: {identifier} has only {distinct_count} distinct values "
                f"(threshold: {MIN_DISCRIMINATION_THRESHOLD})"
            )
            failing_enrichments.append({
                "signal_type": signal_type,
                "enrichment_key": enrichment_key,
                "distinct_count": distinct_count
            })
        else:
            logger.info(
                f"PASS: {identifier} has {distinct_count} distinct values "
                f"(threshold: {MIN_DISCRIMINATION_THRESHOLD})"
            )
            passing_enrichments.append({
                "signal_type": signal_type,
                "enrichment_key": enrichment_key,
                "distinct_count": distinct_count
            })
    
    ws_write("enrichment_discrimination_check", [{
        "check_ts": datetime.now(timezone.utc).isoformat(),
        "signals_checked": len(results),
        "passing_count": len(passing_enrichments),
        "failing_count": len(failing_enrichments),
        "status": "PASS" if not failing_enrichments else "FAIL"
    }])
    
    if failing_enrichments:
        logger.error(
            f"Enrichment discrimination check FAILED: {len(failing_enrichments)} "
            f"of {len(results)} enrichments below threshold {MIN_DISCRIMINATION_THRESHOLD}"
        )
        for f in failing_enrichments:
            logger.error(f"  - {f['signal_type']}::{f['enrichment_key']}: {f['distinct_count']} distinct")
        return False
    
    logger.info(
        f"Enrichment discrimination check PASSED: all {len(passing_enrichments)} "
        f"enrichments meet threshold {MIN_DISCRIMINATION_THRESHOLD}"
    )
    return True

if __name__ == "__main__":
    try:
        success = check_enrichment_discrimination()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Verification script failed: {e}")
        sys.exit(1)