#!/usr/bin/env python3
"""
definition_history_gap_diagnostic.py

Diagnostic utility investigating why mcp_definition_history is empty (0 rows).
Checks:
  (1) Whether definition_change_monitor.py is running and heartbeat-healthy
  (2) Whether mcp_scanner captures definition snapshots
  (3) Whether the history writer daemon is wired correctly
  (4) Recent server definition hashes from mcp_fingerprints vs current registry entries

Outputs a JSON report identifying where the history capture pipeline is broken.
"""

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

# ---------------------------------------------------------------------------
# Attempt to import project-specific modules with graceful degradation
# ---------------------------------------------------------------------------
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import sqlite3
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ---------------------------------------------------------------------------
# Dataclasses for structured reporting
# ---------------------------------------------------------------------------

@dataclass
class HealthCheckResult:
    name: str
    status: str  # "PASS", "FAIL", "WARN", "SKIP", "ERROR"
    message: str = ""
    details: dict = field(default_factory=dict)
    exception: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class DiagnosticReport:
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    hostname: str = field(default_factory=lambda: os.environ.get("HOSTNAME", "unknown"))
    pid: int = field(default_factory=lambda: os.getpid())
    python_executable: str = field(default_factory=lambda: sys.executable)
    definition_history_row_count: int = -1
    overall_status: str = "UNKNOWN"
    summary: str = ""
    checks: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    pipeline_diagram: dict = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

class DatabaseHelper:
    """Encapsulates all database interaction for the diagnostic."""

    def __init__(self, db_path: Optional[str] = None):
        if not HAS_SQLITE:
            self.conn = None
            self.db_path = None
            return

        if db_path is None:
            # Common locations for the sentinel database
            candidates = [
                os.environ.get("SENTINEL_DB_PATH"),
                os.environ.get("MCP_SENTINEL_DB"),
                "/var/lib/sentinel/sentinel.db",
                "/tmp/sentinel.db",
                "./sentinel.db",
                str(Path.home() / ".sentinel" / "sentinel.db"),
            ]
            for c in candidates:
                if c and Path(c).exists():
                    db_path = c
                    break

        self.db_path = db_path
        self.conn = None
        if db_path:
            try:
                self.conn = sqlite3.connect(db_path, timeout=5.0)
                self.conn.row_factory = sqlite3.Row
            except Exception as e:
                self.conn = None

    def close(self):
        if self.conn:
            self.conn.close()

    def query(self, sql: str, params: tuple = ()) -> list:
        if not self.conn:
            return []
        try:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_table_names(self) -> list:
        return self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )

    def get_row_count(self, table: str) -> int:
        rows = self.query(f"SELECT COUNT(*) as cnt FROM {table}")
        return rows[0]["cnt"] if rows else -1


# ---------------------------------------------------------------------------
# Check 1: definition_change_monitor.py heartbeat
# ---------------------------------------------------------------------------

def check_monitor_heartbeat(db: DatabaseHelper) -> HealthCheckResult:
    """
    Verify definition_change_monitor.py is running and updating a heartbeat
    row in the database (or a lock file, or process list).
    """
    start = time.time()
    result = HealthCheckResult(
        name="definition_change_monitor.py heartbeat",
        status="SKIP",
        message="Unable to check (psutil not available or no DB)",
    )

    if not HAS_PSUTIL:
        result.message = "psutil not installed; cannot enumerate processes"
        return result

    # Search for the monitor process by name patterns
    monitor_processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "status"]):
        try:
            pinfo = proc.info
            name = pinfo.get("name", "")
            cmdline = " ".join(pinfo.get("cmdline") or [])
            if "definition_change_monitor" in cmdline.lower() or \
               "definition-change-monitor" in cmdline.lower():
                monitor_processes.append(pinfo)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    result.details["processes_found"] = len(monitor_processes)
    result.details["processes"] = monitor_processes

    if not monitor_processes:
        result.status = "FAIL"
        result.message = "No definition_change_monitor.py process found in process list"
        result.duration_ms = (time.time() - start) * 1000
        return result

    # Check heartbeat table (if it exists)
    heartbeat_rows = db.query(
        "SELECT * FROM monitor_heartbeat ORDER BY id DESC LIMIT 1"
    )
    result.details["heartbeat_rows"] = heartbeat_rows

    # Also check for lockfile heartbeat
    lockfile_paths = [
        "/var/run/definition_change_monitor.lock",
        "/tmp/definition_change_monitor.lock",
        str(Path.home() / ".sentinel" / "monitor.lock"),
    ]
    lockfile_status = {}
    for lf in lockfile_paths:
        lockfile_status[lf] = Path(lf).exists()
    result.details["lockfiles"] = lockfile_status

    if heartbeat_rows:
        last_hb = heartbeat_rows[0]
        last_time = last_hb.get("last_heartbeat") or last_hb.get("updated_at") or last_hb.get("ts")
        if last_time:
            try:
                last_dt = datetime.fromisoformat(str(last_time).replace("Z", "+00:00"))
                age = datetime.utcnow() - last_dt.replace(tzinfo=None)
                result.details["last_heartbeat_age_seconds"] = age.total_seconds()
                if age.total_seconds() > 120:
                    result.status = "WARN"
                    result.message = f"Monitor process running but heartbeat is stale ({age.total_seconds():.0f}s old)"
                else:
                    result.status = "PASS"
                    result.message = "Monitor process running with healthy heartbeat"
            except Exception:
                result.status = "WARN"
                result.message = "Monitor running; heartbeat timestamp unreadable"
    else:
        result.status = "WARN"
        result.message = "Monitor process found but no heartbeat row in database"

    result.duration_ms = (time.time() - start) * 1000
    return result


# ---------------------------------------------------------------------------
# Check 2: mcp_scanner snapshot capture
# ---------------------------------------------------------------------------

def check_scanner_snapshots(db: DatabaseHelper) -> HealthCheckResult:
    """
    Verify mcp_scanner has captured definition snapshots into a staging table
    (e.g., mcp_definition_snapshots or similar).
    """
    start = time.time()
    result = HealthCheckResult(
        name="mcp_scanner snapshot capture",
        status="SKIP",
        message="No DB connection",
    )

    if not db.conn:
        result.message = "No database connection"
        return result

    # Discover snapshot-related tables
    all_tables = [t["name"] for t in db.get_table_names()]
    result.details["all_tables"] = all_tables

    snapshot_candidates = [
        "mcp_definition_snapshots",
        "definition_snapshots",
        "mcp_snapshots",
        "server_definition_snapshots",
        "snapshots",
    ]

    snapshot_table = None
    for candidate in snapshot_candidates:
        if candidate in all_tables:
            snapshot_table = candidate
            break

    result.details["snapshot_table_found"] = snapshot_table
    result.details["tables_checked"] = snapshot_candidates

    if not snapshot_table:
        result.status = "FAIL"
        result.message = (
            f"No snapshot table found among {len(all_tables)} tables. "
            "mcp_scanner may not be writing snapshots."
        )
        result.duration_ms = (time.time() - start) * 1000
        return result

    row_count = db.get_row_count(snapshot_table)
    result.details["snapshot_row_count"] = row_count

    # Sample recent rows
    recent = db.query(f"SELECT * FROM {snapshot_table} ORDER BY 1 DESC LIMIT 5")
    result.details["sample_rows"] = recent

    if row_count == 0:
        result.status = "FAIL"
        result.message = f"Snapshot table '{snapshot_table}' exists but is empty (0 rows)"
    elif row_count < 10:
        result.status = "WARN"
        result.message = f"Snapshot table has only {row_count} rows; expected more"
    else:
        result.status = "PASS"
        result.message = f"Snapshot table has {row_count} rows"

    result.duration_ms = (time.time() - start) * 1000
    return result


# ---------------------------------------------------------------------------
# Check 3: History writer daemon wiring
# ---------------------------------------------------------------------------

def check_history_writer_daemon(db: DatabaseHelper) -> HealthCheckResult:
    """
    Verify the history writer daemon is:
      - Running as a process
      - Connected to the DB
      - Consuming from the snapshot table or an event queue
      - Writing to mcp_definition_history
    """
    start = time.time()
    result = HealthCheckResult(
        name="history writer daemon wiring",
        status="SKIP",
        message="psutil not available",
    )

    if not HAS_PSUTIL:
        result.details["reason"] = "psutil not installed"
        return result

    # Locate daemon process
    daemon_processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if any(kw in cmdline.lower() for kw in [
                "history_writer", "history-writer", "history_writer_daemon",
                "mcp_history_writer", "definition_history_writer"
            ]):
                daemon_processes.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    result.details["daemon_processes"] = daemon_processes

    if not daemon_processes:
        result.status = "FAIL"
        result.message = "No history writer daemon process found"
        result.duration_ms = (time.time() - start) * 1000
        return result

    # Check mcp_definition_history table existence
    history_count = -1
    history_exists = False
    all_tables = [t["name"] for t in db.get_table_names()] if db.conn else []
    history_exists = "mcp_definition_history" in all_tables
    result.details["mcp_definition_history_exists"] = history_exists
    result.details["all_tables"] = all_tables

    if history_exists:
        history_count = db.get_row_count("mcp_definition_history")
        result.details["history_row_count"] = history_count

        # Check schema
        schema_rows = db.query("PRAGMA table_info(mcp_definition_history)")
        result.details["history_schema"] = schema_rows

        # Check for recent inserts
        recent_history = db.query(
            "SELECT * FROM mcp_definition_history ORDER BY 1 DESC LIMIT 5"
        )
        result.details["recent_history_rows"] = recent_history

        # Check for gaps (empty history despite snapshots)
        snapshot_count = result.details.get("snapshot_row_count", -1)
        if history_count == 0 and snapshot_count and snapshot_count > 0:
            result.details["gap_detected"] = True
            result.details["gap_message"] = (
                f"Snapshots exist ({snapshot_count}) but history is empty. "
                "Writer daemon is not consuming snapshots."
            )

    # Check if daemon has any open DB connections (if possible)
    result.status = "PASS" if daemon_processes else "FAIL"
    result.message = (
        f"History writer daemon found (PID(s): {[p['pid'] for p in daemon_processes]}). "
        f"History table row count: {history_count}"
    )

    result.duration_ms = (time.time() - start) * 1000
    return result


# ---------------------------------------------------------------------------
# Check 4: Fingerprint hash comparison
# ---------------------------------------------------------------------------

def check_fingerprint_hash_comparison(db: DatabaseHelper) -> HealthCheckResult:
    """
    Compare server definition hashes from mcp_fingerprints table against
    current registry entries to detect changes that should have been captured.
    """
    start = time.time()
    result = HealthCheckResult(
        name="fingerprint hash comparison (mcp_fingerprints vs registry)",
        status="SKIP",
        message="No DB connection",
    )

    if not db.conn:
        result.details["reason"] = "No database connection"
        return result

    all_tables = [t["name"] for t in db.get_table_names()]

    # Locate fingerprints table
    fp_candidates = [
        "mcp_fingerprints",
        "server_fingerprints",
        "definition_fingerprints",
        "fingerprints",
    ]
    fp_table = next((t for t in fp_candidates if t in all_tables), None)
    result.details["fingerprint_table"] = fp_table
    result.details["tables_checked"] = fp_candidates

    # Locate current registry table
    registry_candidates = [
        "mcp_registry",
        "mcp_servers",
        "server_registry",
        "servers",
        "registry",
    ]
    reg_table = next((t for t in registry_candidates if t in all_tables), None)
    result.details["registry_table"] = reg_table
    result.details["registry_candidates_checked"] = registry_candidates

    if not fp_table:
        result.status = "FAIL"
        result.message = "No fingerprints table found; cannot compare hashes"
        result.duration_ms = (time.time() - start) * 1000
        return result

    fp_rows = db.query(f"SELECT * FROM {fp_table} ORDER BY 1 DESC LIMIT 20")
    result.details["fingerprint_sample"] = fp_rows
    result.details["fingerprint_count"] = len(fp_rows)

    if not reg_table:
        result.status = "WARN"
        result.message = "Fingerprints table exists but no registry table found for comparison"
        result.duration_ms = (time.time() - start) * 1000
        return result

    reg_rows = db.query(f"SELECT * FROM {reg_table} ORDER BY 1 DESC LIMIT 20")
    result.details["registry_sample"] = reg_rows
    result.details["registry_count"] = len(reg_rows)

    # Detect hash mismatches (registry has entries but no fingerprint, or vice versa)
    fp_hashes = {str(r.get("server_name") or r.get("name") or r.get("id", "")): r
                 for r in fp_rows}
    reg_hashes = {str(r.get("server_name") or r.get("name") or r.get("id", "")): r
                  for r in reg_rows}

    # Find servers in registry with no fingerprint entry
    missing_fingerprints = [
        {"server": s, "registry_entry": reg_hashes[s]}
        for s in reg_hashes if s not in fp_hashes
    ]
    result.details["missing_fingerprints"] = missing_fingerprints

    # Find fingerprint entries with no registry entry (orphaned)
    orphaned_fingerprints = [
        {"fingerprint_entry": fp_hashes[s], "server": s}
        for s in fp_hashes if s not in reg_hashes
    ]
    result.details["orphaned_fingerprints"] = orphaned_fingerprints

    if missing_fingerprints:
        result.status = "WARN"
        result.message = (
            f"{len(missing_fingerprints)} server(s) in registry have no fingerprint entry. "
            "Changes may not be tracked."
        )
    elif orphaned_fingerprints:
        result.status = "WARN"
        result.message = (
            f"{len(orphaned_fingerprints)} fingerprint(s) have no corresponding registry entry"
        )
    else:
        result.status = "PASS"
        result.message = "All registry servers have corresponding fingerprint entries"

    result.duration_ms = (time.time() - start) * 1000
    return result


# ---------------------------------------------------------------------------
# Additional diagnostic checks
# ---------------------------------------------------------------------------

def check_database_connectivity(db: DatabaseHelper) -> HealthCheckResult:
    """Verify the database file exists and is accessible."""
    start = time.time()
    result = HealthCheckResult(
        name="database connectivity",
        status="PASS",
        message="OK",
    )

    if not db.db_path:
        result.status = "FAIL"
        result.message = "No database path configured or found"
        return result

    result.details["db_path"] = db.db_path
    result.details["db_exists"] = Path(db.db_path).exists()

    if not result.details["db_exists"]:
        result.status = "FAIL"
        result.message = f"Database file not found at {db.db_path}"
        return result

    result.details["db_size_bytes"] = Path(db.db_path).stat().st_size

    tables = db.get_table_names()
    result.details["table_count"] = len(tables)
    result.details["tables"] = [t["name"] for t in tables]

    # Primary target: mcp_definition_history
    target_count = db.get_row_count("mcp_definition_history")
    result.details["mcp_definition_history_count"] = target_count
    if target_count == 0:
        result.details["ZERO_HISTORY_ROWS"] = True

    result.duration_ms = (time.time() - start) * 1000
    return result


def check_mcp_definition_history_schema(db: DatabaseHelper) -> HealthCheckResult:
    """Inspect the schema of mcp_definition_history for completeness."""
    start = time.time()
    result = HealthCheckResult(
        name="mcp_definition_history schema",
        status="SKIP",
        message="Table does not exist",
    )

    all_tables = [t["name"] for t in db.get_table_names()] if db.conn else []
    if "mcp_definition_history" not in all_tables:
        result.details["tables_found"] = all_tables
        return result

    schema = db.query("PRAGMA table_info(mcp_definition_history)")
    result.details["schema"] = schema

    expected_columns = ["server_name", "definition", "captured_at", "hash"]
    actual_columns = [col["name"] for col in schema]
    result.details["actual_columns"] = actual_columns
    result.details["missing_columns"] = [c for c in expected_columns if c not in actual_columns]

    if result.details["missing_columns"]:
        result.status = "FAIL"
        result.message = f"Missing columns: {result.details['missing_columns']}"
    else:
        result.status = "PASS"
        result.message = "Schema is complete"

    result.duration_ms = (time.time() - start) * 1000
    return result


def check_configuration_files() -> HealthCheckResult:
    """Check for relevant configuration files."""
    start = time.time()
    result = HealthCheckResult(
        name="configuration files",
        status="PASS",
        message="OK",
    )

    config_dirs = [
        Path("/etc/sentinel"),
        Path("/etc/mcp"),
        Path.home() / ".sentinel",
        Path.home() / ".config" / "sentinel",
        Path("."),
    ]

    config_files = {}
    for d in config_dirs:
        if d.exists():
            config_files[str(d)] = [str(f) for f in d.iterdir() if f.is_file()]

    result.details["config_dirs_found"] = list(config_files.keys())
    result.details["config_files"] = config_files

    if not config_files:
        result.status = "WARN"
        result.message = "No sentinel/mcp configuration directories found"

    result.duration_ms = (time.time() - start) * 1000
    return result


def check_log_files() -> HealthCheckResult:
    """Scan log files for relevant error messages."""
    start = time.time()
    result = HealthCheckResult(
        name="log file analysis",
        status="PASS",
        message="No relevant log entries found",
    )

    log_patterns = [
        "definition_change_monitor",
        "mcp_scanner",
        "history_writer",
        "mcp_definition_history",
        "definition_history_gap",
        "snapshot",
        "heartbeat",
    ]

    log_dirs = [
        Path("/var/log/sentinel"),
        Path("/var/log/mcp"),
        Path.home() / ".sentinel" / "logs",
        Path("/tmp"),
    ]

    relevant_entries = []
    for log_dir in log_dirs:
        if not log_dir.exists():
            continue
        for log_file in log_dir.glob("*.log"):
            try:
                # Read last 200 lines
                lines = log_file.read_text(errors="ignore").splitlines()
                for i, line in enumerate(lines[-200:]):
                    lower = line.lower()
                    if any(p in lower for p in log_patterns):
                        relevant_entries.append({
                            "file": str(log_file),
                            "line_number": len(lines) - 200 + i + 1,
                            "text": line.strip()[:200],
                        })
            except Exception:
                pass

    result.details["log_dirs_scanned"] = [str(d) for d in log_dirs if d.exists()]
    result.details["relevant_entries"] = relevant_entries[-20:]  # Last 20

    if relevant_entries:
        result.status = "WARN"
        result.message = f"Found {len(relevant_entries)} relevant log entries"
        result.details["entry_count"] = len(relevant_entries)
    else:
        result.status = "PASS"
        result.message = "No relevant log entries found"

    result.duration_ms = (time.time() - start) * 1000
    return result


def check_system_resources() -> HealthCheckResult:
    """Check CPU, memory, and disk space for resource-exhaustion issues."""
    start = time.time()
    result = HealthCheckResult(
        name="system resources",
        status="PASS",
        message="OK",
    )

    if not HAS_PSUTIL:
        result.details["note"] = "psutil not available"
        return result

    try:
        result.details["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        result.details["memory_percent"] = psutil.virtual_memory().percent
        result.details["disk_usage_percent"] = psutil.disk_usage("/").percent

        warnings = []
        if result.details["cpu_percent"] > 90:
            warnings.append(f"High CPU: {result.details['cpu_percent']}%")
        if result.details["memory_percent"] > 90:
            warnings.append(f"High memory: {result.details['memory_percent']}%")
        if result.details["disk_usage_percent"] > 90:
            warnings.append(f"Low disk space: {result.details['disk_usage_percent']}%")

        if warnings:
            result.status = "WARN"
            result.message = "; ".join(warnings)
        else:
            result.message = f"CPU={result.details['cpu_percent']}%, Mem={result.details['memory_percent']}%, Disk={result.details['disk_usage_percent']}%"
    except Exception as e:
        result.status = "ERROR"
        result.message = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


# ---------------------------------------------------------------------------
# Pipeline diagram builder
# ---------------------------------------------------------------------------

def build_pipeline_diagram(
    db: DatabaseHelper,
    checks: list[HealthCheckResult]
) -> dict:
    """Construct a visual pipeline diagram with component health."""
    diagram = {
        "pipeline_stages": [
            {
                "id": 1,
                "name": "definition_change_monitor.py",
                "type": "monitor",
                "status": "UNKNOWN",
                "details": {},
            },
            {
                "id": 2,
                "name": "mcp_scanner (snapshot capture)",
                "type": "scanner",
                "status": "UNKNOWN",
                "details": {},
            },
            {
                "id": 3,
                "name": "snapshot table (staging)",
                "type": "storage",
                "status": "UNKNOWN",
                "details": {},
            },
            {
                "id": 4,
                "name": "history_writer_daemon",
                "type": "writer",
                "status": "UNKNOWN",
                "details": {},
            },
            {
                "id": 5,
                "name": "mcp_definition_history (target)",
                "type": "target",
                "status": "UNKNOWN",
                "details": {},
            },
        ],
        "broken_stage": None,
        "gap_location": None,
    }

    for check in checks:
        if "monitor" in check.name.lower() and "heartbeat" in check.name.lower():
            diagram["pipeline_stages"][0]["status"] = check.status
            diagram["pipeline_stages"][0]["details"] = check.details
        elif "scanner" in check.name.lower() or "snapshot" in check.name.lower():
            diagram["pipeline_stages"][1]["status"] = check.status
            diagram["pipeline_stages"][1]["details"] = check.details
            diagram["pipeline_stages"][2]["status"] = check.status
            diagram["pipeline_stages"][2]["details"] = check.details
        elif "history writer" in check.name.lower():
            diagram["pipeline_stages"][3]["status"] = check.status
            diagram["pipeline_stages"][3]["details"] = check.details
        elif "mcp_definition_history schema" in check.name.lower():
            diagram["pipeline_stages"][4]["status"] = check.status
            diagram["pipeline_stages"][4]["details"] = check.details

    # Determine where the pipeline is broken
    for i, stage in enumerate(diagram["pipeline_stages"]):
        if stage["status"] in ("FAIL", "ERROR"):
            diagram["broken_stage"] = i + 1
            diagram["gap_location"] = (
                f"Pipeline broken at stage {i+1}: {stage['name']} "
                f"(status: {stage['status']})"
            )
            break
        elif stage["status"] == "WARN":
            if diagram["broken_stage"] is None:
                diagram["broken_stage"] = i + 1
                diagram["gap_location"] = (
                    f"Pipeline degraded at stage {i+1}: {stage['name']} "
                    f"(status: {stage['status']})"
                )

    return diagram


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def generate_recommendations(checks: list[HealthCheckResult], diagram: dict) -> list[str]:
    """Generate actionable recommendations based on check results."""
    recs = []

    for check in checks:
        if check.name == "database connectivity":
            if check.status == "FAIL":
                recs.append(
                    "ACTION: Configure the correct database path via SENTINEL_DB_PATH environment variable. "
                    "The diagnostic could not locate the sentinel database."
                )
        elif "heartbeat" in check.name.lower():
            if check.status == "FAIL":
                recs.append(
                    "ACTION: Start definition_change_monitor.py. "
                    "The monitor process is not running. "
                    "Run: python definition_change_monitor.py --daemon"
                )
            elif check.status == "WARN":
                recs.append(
                    "ACTION: definition_change_monitor.py is running but its heartbeat is stale. "
                    "Check if the process is blocked or deadlocked. "
                    "Consider restarting: kill -HUP <pid> or restart the service."
                )
        elif "scanner" in check.name.lower():
            if check.status == "FAIL":
                recs.append(
                    "ACTION: mcp_scanner is not capturing snapshots. "
                    "Verify mcp_scanner.py is scheduled (cron/systemd timer) and "
                    "that the snapshot table is correctly configured in sentinel config."
                )
        elif "history writer" in check.name.lower():
            if check.status == "FAIL":
                recs.append(
                    "ACTION: Start the history_writer_daemon. "
                    "The daemon is not running. "
                    "Run: python history_writer_daemon.py --daemon"
                )
            if check.details.get("gap_detected"):
                recs.append(
                    "ACTION: History writer daemon is running but not consuming snapshots. "
                    "Check the queue/pipe between scanner and writer. "
                    "Verify the writer's --source-table or --queue flag matches the snapshot table."
                )
        elif "fingerprint" in check.name.lower():
            if check.status == "WARN" and check.details.get("missing_fingerprints"):
                recs.append(
                    f"ACTION: {len(check.details['missing_fingerprints'])} servers have no fingerprint. "
                    "Run mcp_scanner to capture current server definitions, then the history writer "
                    "will have entries to process."
                )
        elif "schema" in check.name.lower():
            if check.status == "FAIL":
                recs.append(
                    f"ACTION: mcp_definition_history table is missing columns: {check.details.get('missing_columns', [])}. "
                    "Run migrations: python -m sentinel.db migrate"
                )

    if diagram.get("broken_stage"):
        recs.append(
            f"DIAGNOSIS: Pipeline is broken at stage {diagram['broken_stage']}: {diagram.get('gap_location', 'unknown')}. "
            "Fix the identified stage before history will accumulate."
        )

    if not recs:
        recs.append(
            "DIAGNOSIS: No obvious issues detected. If history is still empty, "
            "check the retention policy or archive/purge jobs that may be deleting rows."
        )

    return recs


# ---------------------------------------------------------------------------
# Main diagnostic runner
# ---------------------------------------------------------------------------

def run_diagnostics(db_path: Optional[str] = None, verbose: bool = False) -> DiagnosticReport:
    """Run all diagnostic checks and return a structured report."""
    report = DiagnosticReport()
    db = DatabaseHelper(db_path)

    try:
        # Run all checks
        checks = [
            check_database_connectivity(db),
            check_monitor_heartbeat(db),
            check_scanner_snapshots(db),
            check_history_writer_daemon(db),
            check_fingerprint_hash_comparison(db),
            check_mcp_definition_history_schema(db),
            check_configuration_files(),
            check_log_files(),
            check_system_resources(),
        ]

        report.checks = [asdict(c) for c in checks]

        # Get the target row count
        if db.conn:
            report.definition_history_row_count = db.get_row_count("mcp_definition_history")

        # Build pipeline diagram
        report.pipeline_diagram = build_pipeline_diagram(db, checks)

        # Generate recommendations
        report.recommendations = generate_recommendations(checks, report.pipeline_diagram)

        # Compute overall status
        statuses = [c.status for c in checks]
        if "ERROR" in statuses:
            report.overall_status = "ERROR"
        elif "FAIL" in statuses:
            report.overall_status = "FAIL"
        elif statuses.count("WARN") > 2:
            report.overall_status = "WARN"
        elif "WARN" in statuses:
            report.overall_status = "PARTIAL"
        elif all(s == "PASS" or s == "SKIP" for s in statuses):
            report.overall_status = "PASS"
        else:
            report.overall_status = "UNKNOWN"

        # Build summary
        pass_count = sum(1 for s in statuses if s == "PASS")
        fail_count = sum(1 for s in statuses if s == "FAIL")
        warn_count = sum(1 for s in statuses if s == "WARN")
        skip_count = sum(1 for s in statuses if s == "SKIP")

        report.summary = (
            f"Ran {len(checks)} checks: {pass_count} passed, {fail_count} failed, "
            f"{warn_count} warnings, {skip_count} skipped. "
            f"mcp_definition_history row count: {report.definition_history_row_count}. "
            f"Pipeline broken: {report.pipeline_diagram.get('broken_stage', 'unknown stage')}. "
            f"{report.pipeline_diagram.get('gap_location', '')}"
        )

    except Exception as e:
        report.overall_status = "ERROR"
        report.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        report.summary = f"Diagnostic runner crashed: {e}"
    finally:
        db.close()

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Diagnostic utility for mcp_definition_history gap investigation"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to sentinel database (or set SENTINEL_DB_PATH env var)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path for JSON report (default: stdout)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Include extra detail in output",
    )
    parser.add_argument(
        "--format",
        choices=["json", "summary", "both"],
        default="both",
        help="Output format",
    )

    args = parser.parse_args()

    report = run_diagnostics(db_path=args.db_path, verbose=args.verbose)

    report_dict = asdict(report)

    # Output
    json_output = json.dumps(report_dict, indent=2, default=str)

    if args.output:
        Path(args.output).write_text(json_output)
        print(f"Report written to {args.output}", file=sys.stderr)

    if args.format in ("json", "both"):
        print(json_output)

    if args.format in ("summary", "both"):
        print("\n" + "=" * 70, file=sys.stderr)
        print("DIAGNOSTIC SUMMARY", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print(f"Overall Status: {report.overall_status}", file=sys.stderr)
        print(f"mcp_definition_history rows: {report.definition_history_row_count}", file=sys.stderr)
        print(f"Pipeline broken at stage: {report.pipeline_diagram.get('broken_stage', 'unknown')}", file=sys.stderr)
        print(f"\n{report.summary}", file=sys.stderr)
        print("\nRecommendations:", file=sys.stderr)
        for i, rec in enumerate(report.recommendations, 1):
            print(f"  {i}. {rec}", file=sys.stderr)

    # Exit code reflects overall status
    exit_codes = {"PASS": 0, "PARTIAL": 1, "WARN": 1, "FAIL": 2, "ERROR": 3, "UNKNOWN": 1}
    sys.exit(exit_codes.get(report.overall_status, 1))


if __name__ == "__main__":
    main()
