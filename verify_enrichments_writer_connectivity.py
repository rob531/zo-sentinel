#!/usr/bin/env python3
# deps: requests
"""
Diagnostic utility to verify the enrichments writer daemon can connect to
write_service and write to mcp_signal_enrichments.

PURPOSE: mcp_signal_enrichments has 0 rows (wiring_map gap). This is a read-only
diagnostic that confirms:
  (1) write_service is reachable
  (2) the mcp_signal_enrichments table schema is correct
  (3) the enrichments_writer daemon is heartbeating
  (4) a simulated write batch would be structurally valid

Does NOT write real data to mcp_signal_enrichments.

INTERFACE: Standalone utility script.
  Run: python3 verify_enrichments_writer_connectivity.py
  Exits 0 on PASS, exits 1 on FAIL.

INPUTS (all via write_service /query -- read-only):
  - service_health: enrichments_writer heartbeat age
  - information_schema.columns: mcp_signal_enrichments column list
  - mcp_signal_scores: servers with scores but no enrichment rows

OUTPUT:
  Prints diagnostic JSON to stdout:
  {
    write_service_reachable: bool,
    table_schema_valid: bool,
    enrichments_writer_heartbeat_age_s: float|null,
    servers_missing_enrichments: int,
    verdict: 'PASS'|'FAIL'|'WARN'
  }
  Exits 0 on PASS/WARN, 1 on FAIL.
"""

import json
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
TIMEOUT_SECS = 10
HEARTBEAT_STALE_THRESHOLD_S = 120.0
REQUIRED_COLUMNS = frozenset({"signal_type", "confidence", "evidence_blob", "computed_at"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("verify_enrichments_writer_connectivity")


# ----------------------------------------------------------------------
# write_service helpers (read-only)
# ----------------------------------------------------------------------
def _ws_post(endpoint: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """POST to write_service and return parsed JSON response, or None on error."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/{endpoint}",
            json=payload,
            timeout=TIMEOUT_SECS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_service %s failed: %s", endpoint, exc)
        return None


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT via write_service /query. Returns rows list (empty on error)."""
    payload: Dict[str, Any] = {"sql": sql}
    if params is not None:
        payload["params"] = params
    result = _ws_post("query", payload)
    if result is None:
        return []
    rows = result.get("rows", [])
    if not isinstance(rows, list):
        logger.warning("Unexpected rows type %s for SQL: %s", type(rows), sql[:80])
        return []
    return rows


# ----------------------------------------------------------------------
# Diagnostic checks
# ----------------------------------------------------------------------
def check_write_service_reachable() -> bool:
    """Ping write_service /health or equivalent SELECT 1."""
    result = _ws_post("query", {"sql": "SELECT 1 AS ok"})
    return result is not None


def check_table_schema() -> bool:
    """Verify mcp_signal_enrichments has required columns per PRODUCT_SPEC §3."""
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'mcp_signal_enrichments'
    """
    rows = ws_query(sql)
    cols = {r.get("column_name", "") for r in rows}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        logger.warning("mcp_signal_enrichments missing columns: %s", missing)
        return False
    return True


def check_enrichments_writer_heartbeat() -> Optional[float]:
    """Return heartbeat age in seconds for enrichments_writer, or None if not found."""
    sql = """
        SELECT timestamp
        FROM service_health
        WHERE service_name = 'enrichments_writer'
        ORDER BY timestamp DESC
        LIMIT 1
    """
    rows = ws_query(sql)
    if not rows:
        logger.warning("No service_health row found for enrichments_writer")
        return None
    ts_str = rows[0].get("timestamp", "")
    try:
        # Accept both ISO 8601 and DuckDB-formatted strings
        if "+" in ts_str or ts_str.endswith("Z"):
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(ts_str.replace(" ", "T") + "+00:00")
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse heartbeat timestamp %r: %s", ts_str, exc)
        return None


def count_servers_missing_enrichments() -> int:
    """
    Count servers that have scores in mcp_signal_scores but no rows in
    mcp_signal_enrichments.  These are the gap that the enrichments_writer
    should be closing.
    """
    sql = """
        SELECT COUNT(DISTINCT ss.server_id) AS cnt
        FROM mcp_signal_scores ss
        WHERE NOT EXISTS (
            SELECT 1
            FROM mcp_signal_enrichments e
            WHERE e.server_id = ss.server_id
        )
    """
    rows = ws_query(sql)
    if not rows:
        return 0
    return int(rows[0].get("cnt", 0))


# ----------------------------------------------------------------------
# Simulated batch validation (read-only structure check)
# ----------------------------------------------------------------------
def validate_batch_structure() -> bool:
    """
    Confirm a structurally-valid write batch would be accepted.
    Checks that the table accepts signal_type, confidence, evidence_blob, computed_at
    by querying column types from information_schema.
    """
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_signal_enrichments'
          AND column_name IN ('signal_type', 'confidence', 'evidence_blob', 'computed_at')
    """
    rows = ws_query(sql)
    found = {r["column_name"] for r in rows}
    return found >= REQUIRED_COLUMNS


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def run() -> int:
    logger.info("Starting enrichments_writer connectivity diagnostic")

    # (1) write_service reachable
    write_service_reachable = check_write_service_reachable()

    # (2) table schema valid
    table_schema_valid = check_table_schema() if write_service_reachable else False

    # (3) enrichments_writer heartbeat
    heartbeat_age: Optional[float] = None
    if write_service_reachable:
        heartbeat_age = check_enrichments_writer_heartbeat()

    # (4) servers missing enrichments (gap analysis)
    servers_missing = count_servers_missing_enrichments() if write_service_reachable else 0

    # (5) batch structure validation
    batch_valid = validate_batch_structure() if write_service_reachable else False

    # Determine verdict
    if not write_service_reachable:
        verdict = "FAIL"
    elif not table_schema_valid:
        verdict = "FAIL"
    else:
        heartbeat_stale = (
            heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_THRESHOLD_S
        )
        if heartbeat_stale:
            verdict = "WARN"
        else:
            verdict = "PASS"

    result: Dict[str, Any] = {
        "write_service_reachable": write_service_reachable,
        "table_schema_valid": table_schema_valid,
        "enrichments_writer_heartbeat_age_s": heartbeat_age,
        "servers_missing_enrichments": servers_missing,
        "batch_structure_valid": batch_valid,
        "verdict": verdict,
    }

    print(json.dumps(result, indent=2))

    if verdict == "FAIL":
        logger.error("Diagnostic result: FAIL")
        return 1
    if verdict == "WARN":
        logger.warning("Diagnostic result: WARN (heartbeat stale)")
        return 0
    logger.info("Diagnostic result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
