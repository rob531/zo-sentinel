# deps: requests
"""Module to verify functional health of the write_service.

Provides a single function `verify_health()` which performs a trivial write and read
operation against a temporary table using the write_service HTTP API.

The function returns ``True`` if the temporary table can be created, a row
inserted, read back, and subsequently dropped.  It returns ``False`` otherwise,
printing diagnostic messages to ``stderr``.
"""

import sys
import uuid
import requests
from typing import Any, Dict, Tuple

# Base URL for the write_service HTTP API
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _execute_sql(sql: str) -> bool:
    """Execute a DDL/DML statement via the write_service ``/execute`` endpoint.

    Parameters
    ----------
    sql: str
        The SQL statement to execute.

    Returns
    -------
    bool
        ``True`` if the request succeeded (HTTP 200 and no error in response),
        ``False`` otherwise.
    """
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/execute",
            json={"sql": sql, "wait": True},
            timeout=10,
        )
        resp.raise_for_status()
        # The service returns JSON; a successful execution typically contains
        # a ``success`` flag.  We treat any 2xx response as success.
        return True
    except Exception as e:
        print(f"[write_service] execute error: {e}", file=sys.stderr)
        return False


def _write_rows(table: str, rows: Dict[str, Any]) -> bool:
    """Insert rows into a table via the write_service ``/write`` endpoint.

    Parameters
    ----------
    table: str
        Target table name.
    rows: dict
        Mapping of column names to values for a single row.

    Returns
    -------
    bool
        ``True`` on success, ``False`` otherwise.
    """
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[write_service] write error: {e}", file=sys.stderr)
        return False


def _query_sql(sql: str, params: Tuple[Any, ...] = ()) -> Tuple[bool, Any]:
    """Run a SELECT query via the write_service ``/query`` endpoint.

    Parameters
    ----------
    sql: str
        Parameterized SELECT statement.
    params: tuple
        Parameters for the query.

    Returns
    -------
    (bool, Any)
        ``(True, rows)`` on success where ``rows`` is the JSON result, or
        ``(False, None)`` on failure.
    """
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query",
            json={"sql": sql, "params": list(params)},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return True, data
    except Exception as e:
        print(f"[write_service] query error: {e}", file=sys.stderr)
        return False, None


def verify_health() -> bool:
    """Verify that the write_service can handle a full cycle of operations.

    The function creates a uniquely‑named temporary table, inserts a single row,
    reads the row back, and finally drops the table.  All operations are performed
    via the write_service HTTP API.  Any failure results in ``False`` and a
    diagnostic message printed to ``stderr``.
    """
    # Generate a unique temporary table name.
    temp_table = f"tmp_verify_{uuid.uuid4().hex[:8]}"
    # Simple schema: a single integer column and a text column.
    create_sql = f"CREATE TABLE {temp_table} (id INTEGER, note TEXT);"
    if not _execute_sql(create_sql):
        print(f"Failed to create temporary table {temp_table}", file=sys.stderr)
        return False

    # Insert a test row.
    test_row = {"id": 1, "note": "health_check"}
    if not _write_rows(temp_table, test_row):
        print(f"Failed to insert row into {temp_table}", file=sys.stderr)
        _execute_sql(f"DROP TABLE IF EXISTS {temp_table}")
        return False

    # Query the row back.
    select_sql = f"SELECT id, note FROM {temp_table} WHERE id = ?;"
    success, result = _query_sql(select_sql, (1,))
    if not success:
        print(f"Failed to query row from {temp_table}", file=sys.stderr)
        _execute_sql(f"DROP TABLE IF EXISTS {temp_table}")
        return False
    # The service typically returns a list of rows under a key like 'rows'.
    # We accept any truthy result that contains the inserted data.
    rows = result.get("rows") if isinstance(result, dict) else None
    if not rows or not any(r.get("id") == 1 and r.get("note") == "health_check" for r in rows):
        print(f"Unexpected query result from {temp_table}: {result}", file=sys.stderr)
        _execute_sql(f"DROP TABLE IF EXISTS {temp_table}")
        return False

    # Clean up the temporary table.
    if not _execute_sql(f"DROP TABLE IF EXISTS {temp_table}"):
        print(f"Failed to drop temporary table {temp_table}", file=sys.stderr)
        return False

    return True


if __name__ == "__main__":
    if verify_health():
        print("PASS: write_service functional health verified.")
    else:
        print("FAIL: write_service functional health check failed.")
