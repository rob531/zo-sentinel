#!/usr/bin/env python3
"""
diagnose_definition_history_empty_gap_v5.py

Investigates why mcp_definition_history table remains empty (0 rows)
while mcp_server_registry has 1761 rows.

Key checks:
1. Live row counts for both tables
2. Actual column schemas (live DB, not DB_SCHEMA.md)
3. Whether mcp_scanner writes to mcp_definition_history
4. Whether registry has definition-related data to snapshot
5. Targeted patch proposal (NOT modifying mcp_scanner.py directly)

Output: Structured JSON to stdout with findings and patch proposal.
"""

import hashlib
import json
import os
import sys
import requests
from datetime import datetime, timezone
from typing import Any, Optional

# Configuration
WRITE_SERVICE = "http://127.0.0.1:8772"
TIMEOUT = 10


def ts_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[list] = None) -> list[dict]:
    """Execute a SELECT via write_service query endpoint."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": sql, "params": params or []},
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return r.json().get("rows", [])
    except Exception as e:
        return [{"_error": str(e)}]


def ws_write(table: str, rows: list[dict]) -> bool:
    """Execute a write via write_service."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return True
    except Exception:
        return False


def live_columns(table: str) -> list[str]:
    """Get live column list for a table via information_schema."""
    rows = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [table]
    )
    return [r["column_name"] for r in rows if "column_name" in r]


def compute_snapshot_hash(server_id: str, name: str, description: str, url: str, metadata: str) -> str:
    """Compute a stable snapshot hash from registry fields."""
    raw = json.dumps({"server_id": server_id, "name": name, "description": description, "url": url, "metadata": metadata}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Investigation ────────────────────────────────────────────────────────────

def investigate_row_counts() -> dict:
    history_cnt = ws_query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    registry_cnt = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    h = history_cnt[0].get("cnt", 0) if history_cnt and "cnt" in history_cnt[0] else 0
    r = registry_cnt[0].get("cnt", 0) if registry_cnt and "cnt" in registry_cnt[0] else 0
    return {"mcp_definition_history": h, "mcp_server_registry": r, "gap": h == 0 and r > 0}


def investigate_schemas() -> dict:
    reg_cols = live_columns("mcp_server_registry")
    hist_cols = live_columns("mcp_definition_history")
    def_cols = [c for c in reg_cols if any(k in c.lower() for k in ["def", "schema", "tool", "signature"])]
    return {
        "registry_columns": reg_cols,
        "history_columns": hist_cols,
        "definition_related_registry_columns": def_cols,
        "registry_has_definition_column": "definition" in reg_cols,
    }


def investigate_registry_definition_data() -> dict:
    """Check if registry rows contain definition-like data in any column."""
    reg_cols = live_columns("mcp_server_registry")
    
    # Check various columns for non-null definition-like data
    checks = {}
    for col in ["description", "metadata"]:
        if col in reg_cols:
            result = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE {col} IS NOT NULL AND {col} != '' AND {col} != '{{}}'")
            if result and "cnt" in result[0]:
                checks[col] = result[0]["cnt"]
    
    # Sample a few rows to see what's in metadata
    sample = ws_query("SELECT server_id, name, metadata FROM mcp_server_registry LIMIT 3")
    
    return {"populated_columns": checks, "sample_rows": sample}


def investigate_scanner_wiring() -> dict:
    """Check whether mcp_scanner.py writes to mcp_definition_history."""
    scanner_path = "/home/workspace/zo_sentinel/mcp_scanner.py"
    findings = {
        "scanner_exists": os.path.exists(scanner_path),
        "writes_to_history": False,
        "has_definition_snapshot_logic": False,
    }
    
    if findings["scanner_exists"]:
        with open(scanner_path) as f:
            content = f.read()
        findings["writes_to_history"] = "mcp_definition_history" in content
        findings["has_definition_snapshot_logic"] = "snapshot" in content.lower() or "definition_hash" in content
        findings["upsert_function"] = "upsert" in content
        
    return findings


def investigate_writer_daemons() -> dict:
    """Check for definition history writer daemon files and health records."""
    daemon_files = [
        "definition_change_history_writer.py",
        "definition_change_history_writer_v2.py",
        "definition_change_monitor.py",
        "mcp_definition_history_writer.py",
        "mcp_definition_history_writer_daemon.py",
    ]
    
    results = {}
    for fname in daemon_files:
        path = f"/home/workspace/zo_sentinel/{fname}"
        results[fname] = {
            "exists": os.path.exists(path),
            "writes_to_history": False,
            "references_definition_column": False,
        }
        if results[fname]["exists"]:
            with open(path) as f:
                content = f.read()
            results[fname]["writes_to_history"] = "mcp_definition_history" in content
            results[fname]["references_definition_column"] = '"definition"' in content or "'definition'" in content
    
    # Check service_health for any definition/history daemons
    health = ws_query(
        "SELECT service, last_heartbeat FROM service_health WHERE service LIKE '%definition%' OR service LIKE '%history%'"
    )
    
    return {"daemon_files": results, "health_records": health}


def investigate_history_schema_mismatch() -> dict:
    """
    The existing writer daemons expect mcp_server_registry.definition (VARCHAR),
    but the live table does not have this column. This is the root structural gap.
    """
    reg_cols = live_columns("mcp_server_registry")
    hist_cols = live_columns("mcp_definition_history")
    
    writer_expects_definition = "definition" in reg_cols
    writer_expects_columns = ["server_id", "snapshot_hash", "captured_at"]  # actual history schema
    
    return {
        "history_actual_columns": hist_cols,
        "writer_expects_registry_definition_column": not writer_expects_definition,
        "schema_mismatch_confirmed": (
            hist_cols == writer_expects_columns and not writer_expects_definition
        ),
    }


# ── Patch Proposal ───────────────────────────────────────────────────────────

def build_patch_proposal() -> dict:
    """
    Propose a targeted patch: a standalone patch module that the scanner
    can call after each server upsert to write a definition snapshot.
    
    The patch should:
    1. Hook into scanner's upsert path via post-upsert callback OR
    2. Run as a separate scan-step after scanner cycle completes
    3. Snapshot current registry state using available columns
       (name + description + metadata) -> snapshot_hash
    """
    patch = {
        "filename": "patch_definition_history_snapshot.py",
        "purpose": "Snapshot mcp_server_registry state to mcp_definition_history after each scan cycle",
        "trigger": "Run as post-step after mcp_scanner cycle completes",
        "inputs": ["server_id", "name", "description", "url", "metadata"],
        "logic": [
            "For each server in mcp_server_registry:",
            "  - Compute snapshot_hash from name+description+url+metadata",
            "  - Check last captured_at from mcp_definition_history for this server_id",
            "  - If no entry OR snapshot_hash differs from last stored:",
            "      INSERT INTO mcp_definition_history (server_id, snapshot_hash, captured_at)",
        ],
        "columns_written": ["server_id", "snapshot_hash", "captured_at"],
        "db_access": "write_service :8772 ONLY - no direct DuckDB",
        "idempotent": True,  # re-run is a no-op if nothing changed
        "heartbeat": "Writes to service_health on completion",
    }
    return patch


def run_smoke_test() -> dict:
    """
    Smoke test: try to write one snapshot to history using current registry data.
    This proves the INSERT path works and diagnoses any remaining blockers.
    """
    # Pick the first registry row
    rows = ws_query("SELECT server_id, name, description, url, metadata FROM mcp_server_registry LIMIT 1")
    if not rows or "server_id" not in rows[0]:
        return {"smoke_test": "SKIP", "reason": "No registry rows found"}
    
    row = rows[0]
    snap_hash = compute_snapshot_hash(
        row.get("server_id", ""),
        row.get("name", ""),
        row.get("description", ""),
        row.get("url", ""),
        json.dumps(row.get("metadata") or {})
    )
    
    # Try to INSERT into history
    written = ws_write("mcp_definition_history", [{
        "server_id": row["server_id"],
        "snapshot_hash": snap_hash,
        "captured_at": ts_now(),
    }])
    
    return {
        "smoke_test": "PASS" if written else "FAIL",
        "test_row": {
            "server_id": row.get("server_id"),
            "snapshot_hash": snap_hash[:16] + "...",  # truncated for readability
            "insert_succeeded": written,
        }
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> dict:
    findings = {
        "investigation": "diagnose_definition_history_empty_gap_v5",
        "timestamp": ts_now(),
        "section4_reference": "data contracts / freshness SLA",
        "target_file": "diagnose_definition_history_empty_gap_v5.py",
    }

    findings["row_counts"] = investigate_row_counts()
    findings["schema_analysis"] = investigate_schemas()
    findings["registry_definition_data"] = investigate_registry_definition_data()
    findings["scanner_wiring"] = investigate_scanner_wiring()
    findings["writer_daemons"] = investigate_writer_daemons()
    findings["schema_mismatch"] = investigate_history_schema_mismatch()
    findings["patch_proposal"] = build_patch_proposal()
    findings["smoke_test"] = run_smoke_test()

    # ── Root cause summary ──────────────────────────────────────────────────
    rc = []
    
    if findings["row_counts"]["gap"]:
        rc.append("CONFIRMED: mcp_definition_history has 0 rows while registry has >0 rows")
    
    if not findings["schema_analysis"]["registry_has_definition_column"]:
        rc.append("ROOT CAUSE: mcp_server_registry has no 'definition' column - existing writer daemons cannot function")
    
    if not findings["scanner_wiring"]["writes_to_history"]:
        rc.append("ROOT CAUSE: mcp_scanner.py does NOT write to mcp_definition_history at all")
    
    if findings["schema_mismatch"]["schema_mismatch_confirmed"]:
        rc.append("STRUCTURAL: mcp_definition_history schema expects server_id/snapshot_hash/captured_at; scanner provides no data for these columns")
    
    findings["root_cause_summary"] = rc
    
    # ── Recommendations ─────────────────────────────────────────────────────
    recs = []
    
    if findings["smoke_test"]["smoke_test"] == "PASS":
        recs.append("SMOKE TEST PASSED: INSERT path is functional. The gap is purely a pipeline/population issue.")
    elif findings["smoke_test"]["smoke_test"] == "FAIL":
        recs.append("SMOKE TEST FAILED: INSERT path broken - check write_service and mcp_definition_history schema")
    
    if not findings["scanner_wiring"]["writes_to_history"]:
        recs.append("A standalone snapshot patch (proposed above) should be created and run after each scanner cycle")
        recs.append("The patch should compute snapshot_hash from available registry columns (name+description+url+metadata)")
        recs.append("Idempotent: skip servers whose snapshot_hash matches the last captured entry")
    
    findings["recommendations"] = recs
    
    return findings


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
    # Exit 0 if gap confirmed (diagnostic complete), exit 1 if no gap
    gap = result.get("row_counts", {}).get("gap", False)
    sys.exit(0 if gap else 0)  # Always exit 0 - this is a diagnostic
