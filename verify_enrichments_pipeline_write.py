#!/usr/bin/env python3
"""
verify_enrichments_pipeline_write.py

Diagnostic script that confirms the enrichments pipeline is writing rows to
mcp_signal_enrichments correctly. Designed to be run standalone or as a smoke
pre-flight before deploying new enrichment writers.

PURPOSE: Addresses the gap that mcp_signal_enrichments has 0 rows despite
mcp_signal_enrichments_schema.py being recently built (2026-06-09T04:47:10).
Diagnoses whether the pipeline writer is reachable, the table schema matches,
and enrichment rows are actually landing.

INTERFACE:
  def run_diagnostic() -> dict:
      Returns dict with keys:
        table_exists: bool
        schema_valid: bool  (has signal_type, score, evidence_blob, computed_at columns)
        row_count: int
        sample_rows: list of up to 3 dicts
        pipeline_writer_reachable: bool  (can import mcp_signal_enrichments_writer)
        errors: list of str

  if __name__ == '__main__': run() runs the diagnostic and prints results.

INPUTS: No user inputs. Reads DB via write_service at 127.0.0.1:8772 using
  POST /query with SQL: SELECT COUNT(*) as cnt FROM mcp_signal_enrichments;
  SELECT signal_type, score, computed_at FROM mcp_signal_enrichments LIMIT 3.

OUTPUT: Prints human-readable diagnostic report to stdout. Returns exit code 0
  if table exists and schema is valid; exit code 1 if table missing or schema
  invalid; exit code 2 if table empty (pipeline not writing). No DB writes.

CONSTRAINTS: stdlib only (requests, json). No DB writes. No direct duckdb
  import. No imports of protected modules. Timeout 10s on write_service calls.

ACCEPTANCE: __main__ self-test with three scenarios:
  1. Happy path: table exists with rows — asserts row_count > 0 and
     sample_rows is non-empty list.
  2. Empty table: asserts exit code 2 and 'empty' in output.lower().
  3. Table missing: asserts exit code 1 and 'missing' in output.lower().
  Uses unittest.mock to patch requests.post for isolation.
  python3 verify_enrichments_pipeline_write.py exits 0 on all three.
"""

import json
import sys
import importlib.util
from typing import Any

import requests

# deps: requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
TIMEOUT_SECONDS = 10


def _query_write_service(sql: str) -> dict[str, Any] | None:
    """Execute a SELECT via write_service POST /query. Returns rows list or None on error."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql, "params": []},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.RequestException:
        return None


def _check_table_exists() -> bool:
    """Return True if mcp_signal_enrichments is listed in write_service schema."""
    try:
        resp = requests.get(
            f"{WRITE_SERVICE_URL}/schema",
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code == 200:
            schema = resp.json()
            tables = schema.get("tables", []) if isinstance(schema, dict) else []
            return "mcp_signal_enrichments" in tables
    except requests.RequestException:
        pass
    # Fallback: try a descriptive query to see if the table is visible
    result = _query_write_service(
        "SELECT COUNT(*) as cnt FROM information_schema.tables "
        "WHERE table_name = 'mcp_signal_enrichments'"
    )
    if result and result.get("rows"):
        return any(r.get("cnt", 0) > 0 for r in result["rows"])
    return False


def _get_row_count() -> int:
    """Return COUNT(*) from mcp_signal_enrichments, or -1 on error."""
    result = _query_write_service("SELECT COUNT(*) as cnt FROM mcp_signal_enrichments")
    if result and result.get("rows"):
        return int(result["rows"][0].get("cnt", 0))
    return -1


def _get_sample_rows(limit: int = 3) -> list[dict[str, Any]]:
    """Fetch up to `limit` sample rows with signal_type, score, computed_at."""
    result = _query_write_service(
        f"SELECT signal_type, score, computed_at FROM mcp_signal_enrichments LIMIT {limit}"
    )
    if result and result.get("rows"):
        return result["rows"]
    return []


def _get_table_columns() -> list[str]:
    """Return list of column names for mcp_signal_enrichments via write_service."""
    # Try information_schema first
    result = _query_write_service(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'mcp_signal_enrichments' ORDER BY ordinal_position"
    )
    if result and result.get("rows"):
        return [r.get("column_name", "") for r in result["rows"]]
    return []


def _check_pipeline_writer_reachable() -> bool:
    """Return True if mcp_signal_enrichments_writer can be imported."""
    # Try direct import first
    spec = importlib.util.find_spec("mcp_signal_enrichments_writer")
    if spec is not None:
        return True
    # Try common paths
    import os
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "mcp_signal_enrichments_writer.py"),
        os.path.join(os.path.dirname(__file__), "enrichments", "mcp_signal_enrichments_writer.py"),
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            return True
    return False


def _validate_schema_columns(columns: list[str]) -> bool:
    """Return True if schema has the required enrichment columns."""
    required = {"signal_type", "score", "evidence_blob", "computed_at"}
    return required.issubset(set(columns))


def run_diagnostic() -> dict[str, Any]:
    """
    Run the full enrichment pipeline diagnostic.

    Returns a dict with:
      table_exists: bool
      schema_valid: bool
      row_count: int
      sample_rows: list[dict]
      pipeline_writer_reachable: bool
      errors: list[str]
    """
    errors: list[str] = []

    # Check table existence
    table_exists = _check_table_exists()
    if not table_exists:
        errors.append("Table 'mcp_signal_enrichments' is missing from the database")
        return {
            "table_exists": False,
            "schema_valid": False,
            "row_count": 0,
            "sample_rows": [],
            "pipeline_writer_reachable": _check_pipeline_writer_reachable(),
            "errors": errors,
        }

    # Get column list and validate schema
    columns = _get_table_columns()
    schema_valid = _validate_schema_columns(columns)
    if not schema_valid:
        missing = {"signal_type", "score", "evidence_blob", "computed_at"} - set(columns)
        errors.append(f"Schema invalid — missing columns: {missing}")

    # Get row count
    row_count = _get_row_count()
    if row_count < 0:
        errors.append("Could not query row count from mcp_signal_enrichments")

    # Get sample rows
    sample_rows: list[dict[str, Any]] = []
    if row_count >= 0:
        sample_rows = _get_sample_rows(limit=3)
        if not sample_rows and row_count > 0:
            errors.append("Row count > 0 but sample query returned no rows")

    # Check pipeline writer reachability
    pipeline_writer_reachable = _check_pipeline_writer_reachable()
    if not pipeline_writer_reachable:
        errors.append(
            "Pipeline writer 'mcp_signal_enrichments_writer' not found — "
            "enrichment rows may not be written"
        )

    return {
        "table_exists": table_exists,
        "schema_valid": schema_valid,
        "row_count": row_count if row_count >= 0 else 0,
        "sample_rows": sample_rows,
        "pipeline_writer_reachable": pipeline_writer_reachable,
        "errors": errors,
    }


def _format_report(diag: dict[str, Any]) -> str:
    """Render the diagnostic result as a human-readable string."""
    lines = ["=" * 60, "ENRICHMENT PIPELINE WRITE DIAGNOSTIC", "=" * 60, ""]

    lines.append(f"  Table exists:             {diag['table_exists']}")
    if not diag["table_exists"]:
        lines.append("  Table status:              TABLE MISSING")
    lines.append(f"  Schema valid:             {diag['schema_valid']}")
    lines.append(f"  Pipeline writer reachable: {diag['pipeline_writer_reachable']}")
    rc = diag["row_count"]
    rc_str = f"{rc} (empty)" if rc == 0 else str(rc)
    lines.append(f"  Row count:                {rc_str}")

    if diag["sample_rows"]:
        lines.append("")
        lines.append("  Sample rows (up to 3):")
        for i, row in enumerate(diag["sample_rows"], 1):
            lines.append(f"    [{i}] {row}")
    else:
        lines.append("  Sample rows:              (none)")

    if diag["errors"]:
        lines.append("")
        lines.append("  ERRORS:")
        for err in diag["errors"]:
            lines.append(f"    - {err}")
    else:
        lines.append("")
        lines.append("  No errors.")

    lines.append("=" * 60)
    return "\n".join(lines)


def run() -> int:
    """Run the diagnostic and print the report. Returns exit code."""
    diag = run_diagnostic()
    print(_format_report(diag))

    if not diag["table_exists"] or not diag["schema_valid"]:
        return 1
    if diag["row_count"] == 0:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Self-test (unittest.mock isolation)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import unittest.mock

    # Scenario 1: Happy path — table exists with rows
    def _happy_post(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        data = kwargs.get("json", {})
        sql = data.get("sql", "")
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        if "information_schema.tables" in sql:
            mock.json.return_value = {"rows": [{"cnt": 1}]}
        elif "information_schema.columns" in sql:
            mock.json.return_value = {
                "rows": [
                    {"column_name": "id"},
                    {"column_name": "signal_type"},
                    {"column_name": "score"},
                    {"column_name": "evidence_blob"},
                    {"column_name": "computed_at"},
                    {"column_name": "dimension"},
                ]
            }
        elif "COUNT(*)" in sql:
            mock.json.return_value = {"rows": [{"cnt": 5}]}
        else:
            mock.json.return_value = {
                "rows": [
                    {"signal_type": "supply_chain", "score": 82.5, "computed_at": "2026-06-09T10:00:00Z"},
                    {"signal_type": "temporal_stability", "score": 91.0, "computed_at": "2026-06-09T11:00:00Z"},
                ]
            }
        return mock

    # Scenario 2: Empty table — table exists, schema valid, row_count = 0
    def _empty_post(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        data = kwargs.get("json", {})
        sql = data.get("sql", "")
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        if "information_schema.tables" in sql:
            mock.json.return_value = {"rows": [{"cnt": 1}]}
        elif "information_schema.columns" in sql:
            mock.json.return_value = {
                "rows": [
                    {"column_name": "id"},
                    {"column_name": "signal_type"},
                    {"column_name": "score"},
                    {"column_name": "evidence_blob"},
                    {"column_name": "computed_at"},
                ]
            }
        elif "COUNT(*)" in sql:
            mock.json.return_value = {"rows": [{"cnt": 0}]}
        else:
            mock.json.return_value = {"rows": []}
        return mock

    # Scenario 3: Table missing — table does not exist
    def _missing_post(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        data = kwargs.get("json", {})
        sql = data.get("sql", "")
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        if "information_schema.tables" in sql:
            mock.json.return_value = {"rows": [{"cnt": 0}]}
        else:
            # Any other query should not be reached for missing table
            mock.json.return_value = {"rows": []}
        return mock

    def _happy_get(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"tables": ["mcp_signal_enrichments", "audit_log"]}
        return mock

    def _empty_get(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"tables": ["mcp_signal_enrichments", "audit_log"]}
        return mock

    def _missing_get(url: str, **kwargs: Any) -> unittest.mock.MagicMock:
        mock = unittest.mock.MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"tables": ["audit_log"]}
        return mock

    # Patch importlib so the writer module always appears reachable
    fake_spec = unittest.mock.MagicMock()
    fake_spec.name = "mcp_signal_enrichments_writer"
    with unittest.mock.patch.object(
        importlib.util, "find_spec", return_value=fake_spec
    ):
        # Scenario 1
        with unittest.mock.patch("requests.post", side_effect=_happy_post):
            with unittest.mock.patch("requests.get", side_effect=_happy_get):
                diag1 = run_diagnostic()
        assert diag1["table_exists"] is True, f"Scenario 1 table_exists: {diag1}"
        assert diag1["schema_valid"] is True, f"Scenario 1 schema_valid: {diag1}"
        assert diag1["row_count"] > 0, f"Scenario 1 row_count: {diag1}"
        assert len(diag1["sample_rows"]) > 0, f"Scenario 1 sample_rows: {diag1}"
        print("  ✓ Scenario 1 (happy path) passed")

        # Scenario 2
        with unittest.mock.patch("requests.post", side_effect=_empty_post):
            with unittest.mock.patch("requests.get", side_effect=_empty_get):
                diag2 = run_diagnostic()
        assert diag2["table_exists"] is True, f"Scenario 2 table_exists: {diag2}"
        assert diag2["schema_valid"] is True, f"Scenario 2 schema_valid: {diag2}"
        assert diag2["row_count"] == 0, f"Scenario 2 row_count: {diag2}"
        assert len(diag2["sample_rows"]) == 0, f"Scenario 2 sample_rows: {diag2}"
        # Verify run() returns exit code 2
        with unittest.mock.patch("requests.post", side_effect=_empty_post):
            with unittest.mock.patch("requests.get", side_effect=_empty_get):
                exit2 = run()
        assert exit2 == 2, f"Scenario 2 exit code: {exit2}"
        assert "empty" in _format_report(diag2).lower(), "Scenario 2 output should mention 'empty'"
        print("  ✓ Scenario 2 (empty table) passed")

        # Scenario 3
        with unittest.mock.patch("requests.post", side_effect=_missing_post):
            with unittest.mock.patch("requests.get", side_effect=_missing_get):
                diag3 = run_diagnostic()
        assert diag3["table_exists"] is False, f"Scenario 3 table_exists: {diag3}"
        assert diag3["row_count"] == 0, f"Scenario 3 row_count: {diag3}"
        # Verify run() returns exit code 1
        with unittest.mock.patch("requests.post", side_effect=_missing_post):
            with unittest.mock.patch("requests.get", side_effect=_missing_get):
                exit1 = run()
        assert exit1 == 1, f"Scenario 3 exit code: {exit1}"
        assert "missing" in _format_report(diag3).lower(), "Scenario 3 output should mention 'missing'"
        print("  ✓ Scenario 3 (table missing) passed")

    print("\nAll self-tests passed.")
    sys.exit(0)
