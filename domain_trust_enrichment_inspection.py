import os
import sys
import logging
import requests
from datetime import datetime, timezone
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/domain_trust_enrichment_inspection.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"

def ws_query(sql):
    """Query DuckDB via write_service."""
    payload = {"sql": sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])

def inspect_domain_trust_cardinality():
    """Check distinct domain_trust scores."""
    sql = """
    SELECT DISTINCT score 
    FROM mcp_signal_scores 
    WHERE signal_type = 'domain_trust' 
    LIMIT 50
    """
    try:
        rows = ws_query(sql)
        distinct_scores = [r.get('score') for r in rows if r.get('score') is not None]
        logger.info(f"Found {len(distinct_scores)} distinct domain_trust scores: {distinct_scores}")
        return distinct_scores
    except Exception as e:
        logger.error(f"Failed to query domain_trust scores: {e}")
        return []

def get_sample_domain_trust_records():
    """Get sample records to understand signal structure."""
    sql = """
    SELECT server_id, score, computed_at, meta
    FROM mcp_signal_scores 
    WHERE signal_type = 'domain_trust'
    LIMIT 10
    """
    try:
        rows = ws_query(sql)
        logger.info(f"Sample records: {json.dumps(rows, default=str)}")
        return rows
    except Exception as e:
        logger.error(f"Failed to get sample records: {e}")
        return []

def get_registry_metadata_fields():
    """Check available metadata fields in mcp_registry_facts."""
    sql = """
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'mcp_registry_facts'
    ORDER BY ordinal_position
    """
    try:
        rows = ws_query(sql)
        cols = [r.get('column_name') for r in rows]
        logger.info(f"mcp_registry_facts columns: {cols}")
        return cols
    except Exception as e:
        logger.error(f"Failed to get registry columns: {e}")
        return []

def main():
    logger.info("=== Domain Trust Inspection Starting ===")
    
    distinct_scores = inspect_domain_trust_cardinality()
    cardinality = len(distinct_scores)
    
    metadata_fields = get_registry_metadata_fields()
    
    sample_records = get_sample_domain_trust_records()
    
    if cardinality < 20:
        logger.warning(f"LOW CARDINALITY detected: {cardinality} distinct values (threshold: 20)")
        logger.info("Enrichment module needed - see domain_trust_enrichment.py generation directive")
        return 1
    else:
        logger.info(f"Cardinality OK: {cardinality} distinct values")
        return 0

if __name__ == "__main__":
    sys.exit(main())