import logging
import sys
import requests
from datetime import datetime, timezone

SERVICE_NAME = "verify_schema_complete"
WRITE_SERVICE_URL = "http://localhost:8772"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

REQUIRED_TABLES = [
    "mcp_server_registry",
    "mcp_signal_scores",
    "mcp_signal_enrichments",
    "mcp_threat_associations",
    "mcp_risk_register",
    "mcp_attestations",
    "mcp_definition_history",
]


def ws_query(sql: str):
    """Query DuckDB via write_service."""
    payload = {
        "table": "duckdb",
        "sql": sql,
        "wait": True
    }
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_table_exists(table_name: str) -> bool:
    """Check if a table exists by querying information_schema.tables."""
    sql = f"""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'main' 
      AND table_name = '{table_name}'
    LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    return len(rows) > 0


def verify_all_tables() -> tuple[bool, list[str]]:
    """Verify all required tables exist. Returns (success, missing_tables)."""
    missing = []
    for table in REQUIRED_TABLES:
        if verify_table_exists(table):
            logger.info(f"  Table '%s' exists", table)
        else:
            logger.warning("  Table '%s' MISSING", table)
            missing.append(table)
    return len(missing) == 0, missing


def verify_table_columns(table_name: str) -> dict:
    """Get column names for a table."""
    sql = f"""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_schema = 'main' 
      AND table_name = '{table_name}'
    ORDER BY ordinal_position
    """
    result = ws_query(sql)
    return result.get("rows", [])


def main():
    logger.info("Verifying schema bootstrap completeness")
    logger.info("Checking %d required tables", len(REQUIRED_TABLES))
    
    success, missing = verify_all_tables()
    
    if success:
        logger.info("All required tables verified successfully")
        for table in REQUIRED_TABLES:
            cols = verify_table_columns(table)
            col_names = [c["column_name"] for c in cols]
            logger.info("  %s columns: %s", table, col_names)
        sys.exit(0)
    else:
        logger.error("Missing tables: %s", missing)
        sys.exit(1)


if __name__ == "__main__":
    main()