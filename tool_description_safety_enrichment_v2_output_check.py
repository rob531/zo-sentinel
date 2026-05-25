import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "tool_description_safety_enrichment_v2_output_check"
WRITE_SERVICE_URL = "http://localhost:8772"


def ws_query(sql: str) -> list:
    import requests
    resp = requests.post(
        WRITE_SERVICE_URL + "/query",
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def main():
    sql = """
    SELECT COUNT(DISTINCT composite_score) as distinct_scores,
           COUNT(*) as total_rows,
           MIN(composite_score) as min_score,
           MAX(composite_score) as max_score
    FROM mcp_signal_enrichments
    WHERE signal_type = 'tool_description_safety'
    """
    
    logger.info("Checking tool_description_safety enrichment diversity...")
    result = ws_query(sql)
    
    if not result:
        logger.error("No rows returned from query")
        sys.exit(1)
    
    row = result[0]
    distinct = row.get("distinct_scores", 0)
    total = row.get("total_rows", 0)
    min_s = row.get("min_score")
    max_s = row.get("max_score")
    
    logger.info(f"Distinct composite_score values: {distinct}")
    logger.info(f"Total rows: {total}")
    logger.info(f"Score range: {min_s} to {max_s}")
    
    if distinct <= 4:
        logger.warning(f"PLATEAU DETECTED: only {distinct} distinct scores (threshold > 4)")
        sys.exit(1)
    
    logger.info(f"OK: {distinct} distinct scores (> 4 threshold)")
    sys.exit(0)


if __name__ == "__main__":
    main()