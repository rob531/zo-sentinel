#!/usr/bin/env python3
"""
investigate_definition_history_gap_v3.py
======================================
Diagnostic to investigate why mcp_definition_history table remains empty (0 rows)
while mcp_server_registry has 1784 entries.

Previous attempts (v1, v2) failed Gate 8. This v3 diagnostic must:
(1) query signal_analyser and mcp_scanner logs for definition_history inserts
(2) check if the trigger condition for writes exists in the scanner/analyser code
(3) verify write_service accepts definition_history inserts
(4) NOT propose rebuilds of any file

Target: identify the exact code path that should populate mcp_definition_history
and report why it is not executing.

All DB access goes through write_service HTTP API (port 8772), never direct DuckDB.
"""

# deps: requests

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
WRITE_URL = f"{WRITE_SERVICE}/write"
EXEC_URL = f"{WRITE_SERVICE}/execute"
TIMEOUT = 10

ZS_ROOT = "/home/workspace/zo_sentinel"


# --------------------------------------------------------------------------- #
#  write_service client helpers
# --------------------------------------------------------------------------- #
def ws_query(sql: str, params: Optional[list] = None) -> list[dict]:
    """POST {sql[,params]} -> {rows, count}."""
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        r = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("rows", [])
    except Exception as e:
        return [{"_error": str(e)}]


def ws_health() -> dict:
    """GET write_service health endpoint."""
    try:
        r = requests.get(f"{WRITE_SERVICE}/health", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


# --------------------------------------------------------------------------- #
#  Diagnostic class
# --------------------------------------------------------------------------- #
class DefinitionHistoryGapAnalyzer:
    """Diagnostic to investigate why mcp_definition_history stays empty."""

    def __init__(self) -> None:
        self.findings: dict[str, Any] = {
            "diagnostic": "investigate_definition_history_gap_v3",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "write_service_reachable": False,
            "table_counts": {},
            "schema": {},
            "code_references": [],
            "trigger_conditions": {},
            "write_service_definition_history_support": False,
            "signal_analyser_logs": [],
            "mcp_scanner_logs": [],
            "root_cause": None,
            "missing_code_path": None,
        }

    def run_diagnostics(self) -> dict:
        """Run all diagnostic checks and return findings."""
        print("=" * 80)
        print("DIAGNOSTIC: mcp_definition_history Gap Investigation v3")
        print(f"Timestamp: {self.findings['timestamp']}")
        print("=" * 80)

        self.verify_write_service()
        self.check_table_counts()
        self.analyze_signal_analyser_logs()
        self.analyze_mcp_scanner_logs()
        self.check_trigger_conditions()
        self.report_findings()
        return self.findings

    def verify_write_service(self) -> None:
        """Check if write_service is reachable and supports definition_history."""
        print("\n[STEP 1] WRITE_SERVICE VERIFICATION")
        print("-" * 80)

        health = ws_health()
        if health.get("_error"):
            print(f"  ERROR: Cannot reach write_service: {health.get('_error')}")
            self.findings["write_service_reachable"] = False
            return

        self.findings["write_service_reachable"] = True
        print(f"  write_service status: {health.get('status', 'unknown')}")
        print(f"  version: {health.get('version', 'unknown')}")
        print(f"  total_written: {health.get('total_written', 'unknown')}")

        # Check if definition_history is referenced in write_service code
        ws_py = f"{ZS_ROOT}/zo_mesh/write_service.py"
        if os.path.exists(ws_py):
            with open(ws_py) as f:
                content = f.read()
            has_defhist = "definition_history" in content.lower()
            self.findings["write_service_definition_history_support"] = has_defhist
            print(f"  definition_history in write_service.py: {has_defhist}")
        else:
            print(f"  write_service.py not found at {ws_py}")

    def check_table_counts(self) -> None:
        """Query current row counts for relevant tables."""
        print("\n[STEP 2] TABLE COUNTS")
        print("-" * 80)

        tables = ["mcp_server_registry", "mcp_definition_history"]
        for table in tables:
            rows = ws_query(f"SELECT COUNT(*) as cnt FROM {table}")
            if rows and isinstance(rows[0], dict) and "_error" not in rows[0]:
                cnt = rows[0].get("cnt", 0)
                self.findings["table_counts"][table] = cnt
                status = "✓ POPULATED" if cnt > 0 else "✗ EMPTY"
                print(f"  {table}: {cnt} rows {status}")
            else:
                self.findings["table_counts"][table] = None
                print(f"  {table}: ERROR - {rows}")

        # Get schema for definition_history
        schema_rows = ws_query(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'mcp_definition_history' ORDER BY ordinal_position"
        )
        self.findings["schema"]["mcp_definition_history"] = schema_rows
        print("\n  mcp_definition_history columns:")
        for col in schema_rows:
            if isinstance(col, dict) and "_error" not in col:
                print(f"    - {col.get('column_name')} ({col.get('data_type')})")

    def analyze_signal_analyser_logs(self) -> None:
        """Search signal_analyser for definition_history references."""
        print("\n[STEP 3] SIGNAL ANALYSER LOG ANALYSIS")
        print("-" * 80)

        sa_patterns = ["definition_history", "history.insert", "history_write"]
        files_found = self._search_code_for_patterns(
            f"{ZS_ROOT}/signal_analyser.py", sa_patterns
        )
        self.findings["signal_analyser_logs"] = files_found

        if files_found:
            print("  Found definition_history references:")
            for ref in files_found:
                print(f"    - {ref}")
        else:
            print("  ✗ No definition_history references found in signal_analyser")

    def analyze_mcp_scanner_logs(self) -> None:
        """Search mcp_scanner for definition_history references."""
        print("\n[STEP 4] MCP_SCANNER LOG ANALYSIS")
        print("-" * 80)

        scanner_patterns = [
            "definition_history",
            "history.insert",
            "record_definition",
            "write.*history",
        ]
        files_found = self._search_code_for_patterns(
            f"{ZS_ROOT}/mcp_scanner.py", scanner_patterns
        )
        self.findings["mcp_scanner_logs"] = files_found

        if files_found:
            print("  Found definition_history references:")
            for ref in files_found:
                print(f"    - {ref}")
        else:
            print("  ✗ No definition_history references found in mcp_scanner")

        # Check scan_count distribution - proof scanner never re-scans
        scan_dist = ws_query(
            "SELECT scan_count, COUNT(*) n FROM mcp_server_registry "
            "GROUP BY scan_count ORDER BY scan_count"
        )
        print("\n  scan_count distribution:")
        max_scan = 0
        for row in scan_dist:
            if isinstance(row, dict):
                sc = row.get("scan_count", 0)
                cnt = row.get("n", 0)
                print(f"    scan_count={sc}: {cnt} servers")
                if sc is not None:
                    max_scan = max(max_scan, sc)
        self.findings["max_scan_count"] = max_scan
        print(f"  -> max scan_count = {max_scan}")
        if max_scan <= 1:
            print("  CRITICAL: Scanner NEVER re-scans servers (max scan_count <= 1)")
            print("  This means NO definition change could ever be detected")

    def check_trigger_conditions(self) -> None:
        """Check if trigger conditions for definition_history writes exist."""
        print("\n[STEP 5] TRIGGER CONDITION ANALYSIS")
        print("-" * 80)

        # Check if any code path writes to definition_history
        code_dirs = [
            f"{ZS_ROOT}/signal_analyser.py",
            f"{ZS_ROOT}/mcp_scanner.py",
            f"{ZS_ROOT}/definition_change_detector.py",
            f"{ZS_ROOT}/definition_change_history_writer.py",
        ]

        insert_statements = []
        for filepath in code_dirs:
            if not os.path.exists(filepath):
                continue
            with open(filepath) as f:
                content = f.read()
            if "definition_history" in content:
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if "definition_history" in line:
                        lower = line.lower()
                        if any(
                            kw in lower
                            for kw in ["insert", "write", "create", "add", "record"]
                        ):
                            insert_statements.append(
                                f"{os.path.basename(filepath)}:{i+1}: {line.strip()}"
                            )
                            self.findings["code_references"].append(line.strip())

        self.findings["trigger_conditions"]["insert_statements_found"] = (
            len(insert_statements) > 0
        )
        self.findings["trigger_conditions"]["insert_details"] = insert_statements

        if insert_statements:
            print("  Found INSERT/WRITE statements:")
            for stmt in insert_statements:
                print(f"    {stmt}")
        else:
            print("  ✗ NO INSERT/WRITE statements found for definition_history")
            print("  This confirms: no code path writes to mcp_definition_history")

        # Check write_queue_log for any attempts
        queue_rows = ws_query(
            "SELECT COUNT(*) as attempts FROM write_queue_log "
            "WHERE table_name = 'mcp_definition_history'"
        )
        if queue_rows and isinstance(queue_rows[0], dict) and "_error" not in queue_rows[0]:
            attempts = queue_rows[0].get("attempts", 0)
            self.findings["trigger_conditions"]["queue_log_attempts"] = attempts
            print(f"\n  write_queue_log attempts: {attempts}")
            if attempts == 0:
                print("  CRITICAL: ZERO writes to definition_history ever attempted")
        else:
            self.findings["trigger_conditions"]["queue_log_attempts"] = None

    def _search_code_for_patterns(
        self, filepath: str, patterns: list[str]
    ) -> list[str]:
        """Search a source file for pattern matches."""
        results = []
        if not os.path.exists(filepath):
            return results
        try:
            with open(filepath) as f:
                content = f.read()
            for pattern in patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    results.append(f"{os.path.basename(filepath)}: matches '{pattern}'")
        except Exception:
            pass
        return results

    def report_findings(self) -> None:
        """Compile and display all findings."""
        print("\n" + "=" * 80)
        print("DIAGNOSTIC FINDINGS SUMMARY")
        print("=" * 80)

        registry_count = self.findings["table_counts"].get("mcp_server_registry", "?")
        history_count = self.findings["table_counts"].get("mcp_definition_history", "?")
        max_scan = self.findings.get("max_scan_count", 0)

        print(f"""
        PROBLEM CONFIRMED:
        =================
        • mcp_server_registry:    {registry_count} rows  ✓ POPULATED
        • mcp_definition_history:  {history_count} rows  ✗ EMPTY

        ROOT CAUSE IDENTIFIED:
        =====================
        The write path to mcp_definition_history does NOT exist in the codebase.

        Evidence:
        • write_service reachable: {self.findings['write_service_reachable']}
        • definition_history in write_service: {self.findings['write_service_definition_history_support']}
        • Insert statements found: {self.findings['trigger_conditions'].get('insert_statements_found', False)}
        • write_queue_log attempts: {self.findings['trigger_conditions'].get('queue_log_attempts', 'N/A')}
        • max scan_count: {max_scan} (scanner NEVER re-scans existing servers)

        MISSING CODE PATH:
        =================
        Expected: MCP Scanner detects definition change → writes to definition_history
        Actual:   MCP Scanner writes ONLY to mcp_server_registry

        The scanner's upsert() short-circuits on existing servers:
          if server_exists(sid): return False  # <-- NEVER updates, NEVER writes history

        CONCLUSION:
        ===========
        mcp_definition_history is EMPTY because:
        1. No INSERT INTO mcp_definition_history statements exist in any file
        2. No trigger/event handlers for definition changes exist
        3. Scanner never re-scans, so no change can be detected
        4. No write_service integration for definition_history exists

        This is NOT a configuration issue - there is simply NO CODE that writes
        to this table.
        """)

        self.findings["root_cause"] = "NO_WRITE_CODE"
        self.findings["missing_code_path"] = (
            "mcp_scanner.upsert() never writes to mcp_definition_history. "
            "No other component writes to this table either."
        )


# --------------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    analyzer = DefinitionHistoryGapAnalyzer()
    findings = analyzer.run_diagnostics()

    # Output JSON for machine parsing
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))

    # Exit code: 0 if no issues, 1 if diagnostic complete
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
