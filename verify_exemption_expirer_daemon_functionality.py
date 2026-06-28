# deps: requests
"""
Verification module for the exemption_expirer daemon.
It inserts a test exemption with an expiry_date in the past, triggers a single
processing cycle of the daemon, and checks that the exemption is either marked
as expired or removed from the `mcp_exemptions` table.
The verification is idempotent and can be run repeatedly.
"""
import requests
import uuid
import datetime
import json
import sys
import time
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Write‑service helper functions – these mirror the helpers used by the
# daemon but are defined locally to avoid import‑time side effects.
# ---------------------------------------------------------------------------
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> Dict[str, Any]:
    """Insert rows into a table via the write_service.
    The service expects a JSON payload with keys ``table``, ``rows`` and ``wait``.
    """
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql: str, params: List[Any] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query via the write_service.
    Returns the list of rows as dictionaries.
    """
    if params is None:
        params = []
    payload = {"sql": sql, "params": params}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])

def ws_execute(sql: str, params: List[Any] = None, wait: bool = True) -> None:
    """Execute a DDL/DML statement (e.g., DELETE) via the write_service.
    """
    if params is None:
        params = []
    payload = {"sql": sql, "params": params, "wait": wait}
    resp = requests.post(f"{WRITE_SERVICE_URL}/execute", json=payload, timeout=30)
    resp.raise_for_status()

# ---------------------------------------------------------------------------
# Test‑exemption handling utilities
# ---------------------------------------------------------------------------
TEST_EXEMPTION_ID = "test-exemption-" + str(uuid.uuid4())

def _cleanup_existing() -> None:
    """Remove any previous test rows – makes the verification idempotent."""
    # The exact column name for the primary key is assumed to be ``exemption_id``.
    # If the schema differs, the DELETE will simply affect zero rows.
    sql = "DELETE FROM mcp_exemptions WHERE exemption_id = ?"
    ws_execute(sql, [TEST_EXEMPTION_ID])

def insert_expired_exemption() -> None:
    """Insert a single exemption whose expiry_date is in the past.
    The row includes the minimal set of columns required by the daemon:
    ``exemption_id`` and ``expiry_date``. Additional columns are optional and are
    omitted to keep the test lightweight.
    """
    past_date = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat() + "Z"
    row = {
        "exemption_id": TEST_EXEMPTION_ID,
        "expiry_date": past_date,
        # The daemon may look for a boolean ``expired`` column; we initialise it
        # to FALSE so that the daemon can update it.
        "expired": False,
    }
    ws_write("mcp_exemptions", [row])

def fetch_exemption() -> Dict[str, Any]:
    """Retrieve the test exemption row, if it exists."""
    sql = "SELECT * FROM mcp_exemptions WHERE exemption_id = ?"
    rows = ws_query(sql, [TEST_EXEMPTION_ID])
    return rows[0] if rows else {}

def is_expired(row: Dict[str, Any]) -> bool:
    """Determine whether the daemon has marked the exemption as expired.
    The daemon may either set a boolean ``expired`` flag or delete the row.
    """
    if not row:
        # Row disappeared – considered expired/removed.
        return True
    # Prefer an explicit ``expired`` column if present.
    if "expired" in row:
        return bool(row.get("expired"))
    # Fallback: if the expiry_date is still in the past we cannot rely on it.
    # Treat the presence of the row as NOT expired.
    return False

# ---------------------------------------------------------------------------
# Daemon interaction – we import the daemon and invoke a single processing cycle.
# Importing the module does not start the daemon; the ``run`` function starts a
# loop, but the ``cycle`` helper performs one iteration.
# ---------------------------------------------------------------------------
def trigger_daemon_cycle() -> None:
    """Import the daemon and run a single processing cycle.
    The import is safe because the daemon guards its execution with the usual
    ``if __name__ == '__main__'`` guard. We only call the ``cycle`` function,
    which performs the work without side effects other than DB updates.
    """
    try:
        from exemption_expirer import cycle  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Failed to import exemption_expirer.cycle: {e}")
    # The daemon's ``cycle`` function may raise; we let exceptions propagate so
    # the verification harness can report a failure.
    cycle()

# ---------------------------------------------------------------------------
# Main verification routine
# ---------------------------------------------------------------------------
def verify_exemption_expirer() -> bool:
    """Run the full verification workflow.
    Returns ``True`` on success, ``False`` otherwise.
    """
    try:
        _cleanup_existing()
        insert_expired_exemption()
        # Give the daemon a moment to notice the new row if it watches the DB
        # asynchronously. A short sleep is harmless and helps avoid race
        # conditions in CI environments.
        time.sleep(0.5)
        trigger_daemon_cycle()
        # Re‑query the row after the daemon has processed it.
        row = fetch_exemption()
        return is_expired(row)
    except Exception as exc:
        # Print the exception details for debugging – the caller will turn this
        # into a FAIL message.
        print(f"Verification error: {exc}", file=sys.stderr)
        return False

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    success = verify_exemption_expirer()
    if success:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)
