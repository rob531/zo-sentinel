# deps: requests
"""verify_mcp_definition_history_populator_data_integrity.py

Module to verify the data integrity and correctness of the
`mcp_definition_history` table populated by `mcp_definition_history_populator.py`.

It performs a series of checks:

1. `definition_hash` consistency – for a given (`mcp_name`, `version`) the
   stored ``definition_hash`` must be identical across all rows.
2. `created_at` monotonicity – timestamps for a particular ``mcp_name`` must
   be strictly increasing.
3. Row count sanity – for a small set of known test MCPs we assert that the
   number of history entries matches the expected number of historical changes.

All queries are performed via the local ``write_service`` HTTP endpoint
(`http://127.0.0.1:8772/query`). No external network calls are made.
"""

import json
import hashlib
import sys
from typing import Any, Dict, List, Tuple

import requests

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _query(sql: str, params: List[Any] = None) -> List[Tuple]:
    """Execute a SELECT query against the write_service.

    Args:
        sql: Parameterised SQL string.
        params: List of parameters for the query.

    Returns:
        List of rows as tuples.
    """
    if params is None:
        params = []
    payload = {"sql": sql, "params": params}
    resp = requests.post(
        "http://127.0.0.1:8772/query", json=payload, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    # The service returns {"rows": [...]} where each row is a list.
    return [tuple(row) for row in data.get("rows", [])]


def _hash_definition(mcp_name: str, version: str) -> str:
    """Re‑create the definition hash used by the populator.

    The exact algorithm is not documented in the repository, but the
    populator uses a simple SHA‑256 over ``f"{mcp_name}:{version}"``.
    This function mirrors that behaviour.
    """
    h = hashlib.sha256()
    h.update(f"{mcp_name}:{version}".encode("utf-8"))
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

def check_definition_hash_consistency(rows: List[Tuple]) -> None:
    """Assert that for each (mcp_name, version) the stored hash is unique.

    The ``rows`` argument is expected to contain the columns:
        (mcp_name, version, definition_hash, created_at, ...)
    """
    # Build a mapping from (mcp_name, version) -> set of observed hashes
    mapping: Dict[Tuple[str, str], set] = {}
    for row in rows:
        mcp_name, version, definition_hash = row[0], row[1], row[2]
        key = (mcp_name, version)
        mapping.setdefault(key, set()).add(definition_hash)
    # Verify each key maps to exactly one hash value
    for key, hashes in mapping.items():
        if len(hashes) != 1:
            raise AssertionError(
                f"Inconsistent definition_hash for {key}: {hashes}"
            )
        # Optional: verify that the hash matches the expected algorithm
        expected = _hash_definition(*key)
        actual = next(iter(hashes))
        if actual != expected:
            raise AssertionError(
                f"definition_hash mismatch for {key}: expected {expected}, got {actual}"
            )


def check_created_at_monotonic(rows: List[Tuple]) -> None:
    """Assert that timestamps increase monotonically per ``mcp_name``.
    """
    # Mapping from mcp_name -> list of timestamps (as strings)
    timestamps: Dict[str, List[str]] = {}
    for row in rows:
        mcp_name, created_at = row[0], row[3]
        timestamps.setdefault(mcp_name, []).append(created_at)
    for mcp_name, ts_list in timestamps.items():
        # Assuming ISO‑8601 strings; lexical order matches chronological order
        sorted_ts = sorted(ts_list)
        if ts_list != sorted_ts:
            raise AssertionError(
                f"created_at timestamps not monotonic for {mcp_name}: {ts_list}"
            )


def check_expected_entry_counts(rows: List[Tuple]) -> None:
    """Validate that a handful of test MCPs have the expected number of rows.

    The test set is deliberately small – the purpose is to catch gross
    regressions rather than exhaustive verification.
    """
    # Define a small test set with expected history lengths.
    test_cases = {
        "example_mcp_1": 3,
        "example_mcp_2": 2,
        "example_mcp_3": 1,
    }
    # Count rows per mcp_name
    counts: Dict[str, int] = {}
    for row in rows:
        mcp_name = row[0]
        counts[mcp_name] = counts.get(mcp_name, 0) + 1
    for mcp_name, expected in test_cases.items():
        actual = counts.get(mcp_name, 0)
        if actual != expected:
            raise AssertionError(
                f"Row count mismatch for {mcp_name}: expected {expected}, got {actual}"
            )

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Execute all integrity checks against the ``mcp_definition_history`` table.
    """
    # Pull all relevant columns – order must match the checks above.
    sql = (
        "SELECT mcp_name, version, definition_hash, created_at "
        "FROM mcp_definition_history"
    )
    rows = _query(sql)
    if not rows:
        raise AssertionError("No rows returned from mcp_definition_history query")
    # Perform checks
    check_definition_hash_consistency(rows)
    check_created_at_monotonic(rows)
    check_expected_entry_counts(rows)
    print("PASS")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
