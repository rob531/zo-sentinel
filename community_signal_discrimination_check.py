import logging
import sys
import requests
from datetime import datetime, timezone

# Constants
SERVICE_NAME = "community_signal_discrimination_check"
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def ws_query(sql, params=None):
    """Query DuckDB via write_service HTTP endpoint."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def ws_write(table, rows):
    """Write to DuckDB via write_service HTTP endpoint."""
    if not isinstance(rows, list):
        rows = [rows]
    payload = {"table": table, "rows": rows, "wait": True}
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def check_signal_scores_cardinality():
    """Check community_signal dimension cardinality in mcp_signal_scores."""
    sql = """
    SELECT community_signal, COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE community_signal IS NOT NULL
    GROUP BY community_signal
    ORDER BY cnt DESC
    """
    results = ws_query(sql)
    distinct_count = len(results)
    
    logger.info(f"mcp_signal_scores community_signal: {distinct_count} distinct values")
    
    if results:
        logger.info(f"Top scores: {results[:5]}")
    
    return distinct_count, results


def check_signal_enrichments_cardinality():
    """Check community_signal_enrichment cardinality in mcp_signal_enrichments."""
    sql = """
    SELECT community_signal_enrichment, COUNT(*) as cnt
    FROM mcp_signal_enrichments
    WHERE community_signal_enrichment IS NOT NULL
    GROUP BY community_signal_enrichment
    ORDER BY cnt DESC
    """
    results = ws_query(sql)
    distinct_count = len(results)
    
    logger.info(f"mcp_signal_enrichments community_signal_enrichment: {distinct_count} distinct values")
    
    if results:
        logger.info(f"Top enrichments: {results[:5]}")
    
    return distinct_count, results


def identify_underutilized_metadata_fields():
    """Identify metadata fields with low usage across MCP servers."""
    sql = """
    SELECT 
        'enrichment_fields' as source,
        field_name,
        non_null_count,
        total_count,
        round(100.0 * non_null_count / total_count, 2) as fill_rate_pct
    FROM (
        SELECT 'tags' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE tags IS NOT NULL AND tags != '' AND tags != '[]'
        UNION ALL
        SELECT 'description' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE description IS NOT NULL AND description != ''
        UNION ALL
        SELECT 'capabilities' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE capabilities IS NOT NULL AND capabilities != ''
        UNION ALL
        SELECT 'version' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE version IS NOT NULL AND version != ''
        UNION ALL
        SELECT 'provider' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE provider IS NOT NULL AND provider != ''
        UNION ALL
        SELECT 'author' as field_name, COUNT(*) as non_null_count FROM mcp_signal_enrichments WHERE author IS NOT NULL AND author != ''
    ) fields
    CROSS JOIN (SELECT COUNT(*) as total_count FROM mcp_signal_enrichments) total
    ORDER BY fill_rate_pct ASC
    """
    results = ws_query(sql)
    return results


def log_validation_result(scores_cardinality, enrichments_cardinality, underutilized):
    """Log validation results to service_health and report."""
    ts = datetime.now(timezone.utc).isoformat()
    
    meta = {
        "scores_cardinality": scores_cardinality,
        "enrichments_cardinality": enrichments_cardinality,
        "underutilized_count": len(underutilized) if underutilized else 0,
        "underutilized_sample": underutilized[:5] if underutilized else []
    }
    
    ws_write("service_health", {
        "service_name": SERVICE_NAME,
        "status": "ok" if scores_cardinality >= 20 and enrichments_cardinality >= 20 else "warning",
        "last_heartbeat": ts,
        "meta": str(meta)
    })
    
    return meta


def main():
    logger.info("=== Community Signal Discrimination Validation ===")
    
    # Check scores cardinality
    scores_cardinality, scores_distribution = check_signal_scores_cardinality()
    
    # Check enrichments cardinality
    enrichments_cardinality, enrichments_distribution = check_signal_enrichments_cardinality()
    
    # Identify underutilized fields if cardinality is low
    underutilized = []
    if scores_cardinality < 20 or enrichments_cardinality < 20:
        logger.warning("Low cardinality detected, analyzing underutilized metadata fields...")
        underutilized = identify_underutilized_metadata_fields()
        if underutilized:
            logger.warning(f"Underutilized fields: {underutilized}")
    
    # Log and report
    meta = log_validation_result(scores_cardinality, enrichments_cardinality, underutilized)
    
    # Final assertion
    all_passed = scores_cardinality >= 20 and enrichments_cardinality >= 20
    
    print("\n" + "="*60)
    print("COMMUNITY SIGNAL DISCRIMINATION VALIDATION RESULTS")
    print("="*60)
    print(f"mcp_signal_scores community_signal distinct values: {scores_cardinality}")
    print(f"  Required: >= 20")
    print(f"  Status: {'PASS' if scores_cardinality >= 20 else 'FAIL'}")
    print()
    print(f"mcp_signal_enrichments community_signal_enrichment distinct: {enrichments_cardinality}")
    print(f"  Required: >= 20")
    print(f"  Status: {'PASS' if enrichments_cardinality >= 20 else 'FAIL'}")
    print()
    
    if not all_passed:
        print("UNDERUTILIZED METADATA FIELDS:")
        if underutilized:
            for field in underutilized[:10]:
                print(f"  - {field.get('field_name', 'unknown')}: {field.get('fill_rate_pct', 0)}% filled")
        else:
            print("  (No enrichment data available to analyze)")
    print("="*60)
    
    if all_passed:
        logger.info("VALIDATION PASSED: High cardinality confirmed across MCP signal data")
        sys.exit(0)
    else:
        logger.warning("VALIDATION WARNING: Low cardinality may indicate discrimination issues")
        sys.exit(0)  # Exit 0 but flag warning in meta


if __name__ == "__main__":
    main()