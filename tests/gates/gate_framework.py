#!/usr/bin/env python3
"""
gate_framework.py -- Shared infrastructure for gate tests.

Design constraints addressed:
    1. DuckDB lock serialization: this process is the sole writer to
       gate_errors.db while it holds the connection. Other processes
       (bootstrap, reader) must release their connection before we open ours.
       We retry with backoff if the file is locked, and fail loudly with a
       clear error if we can't acquire after N attempts.

    2. Schema precondition: on every run we check that required tables exist
       in gate_errors.db. If missing, we stop with a clear "run bootstrap
       first" message rather than failing cryptically on first INSERT.

    3. Throttling: thread-safe global throttle via threading.Lock. All three
       HTTP helpers (query/execute/write) share one throttle gate, so a
       mix of read and write calls still respects the rate limit.

    4. Retry budget: ws_query, ws_execute, and ws_write_row all retry up to
       MAX_RETRIES times with exponential backoff. A 200ms write_service
       blip won't sink a gate run.

    5. Inter-check pause: each check sleeps INTER_CHECK_MS after recording
       its outcome, giving write_service's single writer thread time to
       drain any queued writes from the previous check.

    6. Inter-gate pause: between gates, INTER_GATE_SEC lets the system
       settle before the next gate's setup runs.
"""
import duckdb
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import requests

WS                 = "http://127.0.0.1:8772"
GATE_ERRORS_DB     = "/home/workspace/gate_errors.db"

# Timing knobs -- overridable via env for debug/stress runs
REQUEST_DELAY_MS   = int(os.environ.get("GATE_REQUEST_DELAY_MS", "200"))
INTER_CHECK_MS     = int(os.environ.get("GATE_INTER_CHECK_MS", "250"))
INTER_GATE_SEC     = int(os.environ.get("GATE_INTER_GATE_SEC", "5"))

# Retry budget
MAX_RETRIES        = 3
BACKOFF_BASE_SEC   = 1.0   # attempt 0 -> 1s, attempt 1 -> 2s, attempt 2 -> 4s
BACKOFF_CAP_SEC    = 8.0

# DB lock acquisition
DB_LOCK_MAX_RETRIES = 5
DB_LOCK_BACKOFF_SEC = 1.5

# Tables that must exist in gate_errors.db before any gate can run
REQUIRED_TABLES = {
    "gate_runs", "gate_checks", "gate_errors",
    "error_taxonomy", "canary_history",
    "daemon_state_at_gate", "daemon_cycle_config",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:16]


def _backoff(attempt: int) -> float:
    return min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_CAP_SEC)


# =============================================================================
# Thread-safe rate limiting
# =============================================================================

_throttle_lock = threading.Lock()
_last_request_at = 0.0

def _throttle():
    """Global rate gate. Enforces REQUEST_DELAY_MS between ANY two HTTP calls
    to write_service, regardless of endpoint. Thread-safe."""
    global _last_request_at
    with _throttle_lock:
        elapsed_ms = (time.monotonic() - _last_request_at) * 1000
        if elapsed_ms < REQUEST_DELAY_MS:
            sleep_sec = (REQUEST_DELAY_MS - elapsed_ms) / 1000
            time.sleep(sleep_sec)
        _last_request_at = time.monotonic()


# =============================================================================
# write_service HTTP helpers -- all retry, all throttle
# =============================================================================

def ws_query(sql: str, params: Optional[list] = None) -> list[dict]:
    """SELECT via /query -- returns list of dicts. Retries on transient failure."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            r = requests.post(
                WS + "/query",
                json={"sql": sql, "params": params or []},
                timeout=20,
            )
            if r.status_code == 200:
                return r.json().get("rows", [])
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            last_err = f"request exception: {e}"
        if attempt < MAX_RETRIES - 1:
            time.sleep(_backoff(attempt))
    raise RuntimeError(f"ws_query failed after {MAX_RETRIES} attempts: {last_err}")


def ws_execute(sql: str) -> bool:
    """DDL/DML via /execute -- returns True on success. Retries on transient failure."""
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            r = requests.post(
                WS + "/execute",
                json={"sql": sql, "wait": True},
                timeout=20,
            )
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        if attempt < MAX_RETRIES - 1:
            time.sleep(_backoff(attempt))
    return False


def ws_write_row(table: str, row: dict, mode: str = "insert") -> bool:
    """Insert a single row via /write. Retries on transient failure."""
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            r = requests.post(
                WS + "/write",
                json={
                    "table":    table,
                    "rows":     row,
                    "mode":     mode,
                    "agent_id": "gate_framework",
                    "wait":     True,
                },
                timeout=20,
            )
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        if attempt < MAX_RETRIES - 1:
            time.sleep(_backoff(attempt))
    return False


# =============================================================================
# Gate error DB -- lock-aware, schema-checked
# =============================================================================

class GateDBSchemaError(RuntimeError):
    """Raised when gate_errors.db is missing required tables.
    Actionable: run gate_errors_bootstrap.py first."""


class GateDBLockError(RuntimeError):
    """Raised when we cannot acquire the gate_errors.db file lock after
    DB_LOCK_MAX_RETRIES attempts. Usually means bootstrap or reader
    is still holding a connection."""


def _connect_with_lock_retry(path: str) -> duckdb.DuckDBPyConnection:
    """Open the gate_errors DB, retrying on file-lock contention."""
    last_err = None
    for attempt in range(DB_LOCK_MAX_RETRIES):
        try:
            return duckdb.connect(path)
        except duckdb.IOException as e:
            # DuckDB raises IOException with "Could not set lock" on contention
            last_err = e
            msg = str(e).lower()
            if "lock" in msg or "in use" in msg:
                if attempt < DB_LOCK_MAX_RETRIES - 1:
                    time.sleep(DB_LOCK_BACKOFF_SEC * (attempt + 1))
                    continue
            raise
    raise GateDBLockError(
        f"Could not acquire {path} lock after {DB_LOCK_MAX_RETRIES} attempts: "
        f"{last_err}. Another process (bootstrap, reader) may still hold it."
    )


def _verify_schema(con: duckdb.DuckDBPyConnection):
    """Fail loudly if gate_errors.db is missing required tables."""
    existing = {
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
    }
    missing = REQUIRED_TABLES - existing
    if missing:
        raise GateDBSchemaError(
            f"gate_errors.db is missing required tables: {sorted(missing)}. "
            "Run: python3 /home/workspace/zo_sentinel/tests/gate_errors_bootstrap.py"
        )


class GateErrorDB:
    def __init__(self, path: str = GATE_ERRORS_DB):
        self.path = path
        self.con = _connect_with_lock_retry(path)
        _verify_schema(self.con)

    def close(self):
        if self.con:
            self.con.close()
            self.con = None

    # ---- run lifecycle ----

    def open_run(self, trigger: str = "manual") -> str:
        run_id = _uuid("run_")
        self.con.execute(
            "INSERT INTO gate_runs (run_id, started_at, trigger, gates_planned) "
            "VALUES (?, ?, ?, 0)",
            [run_id, _now(), trigger],
        )
        return run_id

    def close_run(self, run_id: str, duration_ms: int, host_state: str = "unknown"):
        passed = self.con.execute(
            "SELECT COUNT(*) FROM gate_checks WHERE run_id = ? AND status = 'pass'",
            [run_id]
        ).fetchone()[0]
        failed = self.con.execute(
            "SELECT COUNT(*) FROM gate_checks WHERE run_id = ? AND status IN ('fail','error')",
            [run_id]
        ).fetchone()[0]
        planned = self.con.execute(
            "SELECT COUNT(DISTINCT gate_name) FROM gate_checks WHERE run_id = ?",
            [run_id]
        ).fetchone()[0]
        self.con.execute(
            "UPDATE gate_runs SET finished_at = ?, duration_ms = ?, "
            "gates_planned = ?, gates_passed = ?, gates_failed = ?, host_state = ? "
            "WHERE run_id = ?",
            [_now(), duration_ms, planned, passed, failed, host_state, run_id],
        )

    # ---- per-check records ----

    def record_check(self, run_id: str, gate_name: str, check_name: str,
                     status: str, duration_ms: int, details: str = "") -> str:
        check_id = _uuid("chk_")
        self.con.execute(
            "INSERT INTO gate_checks (check_id, run_id, gate_name, check_name, "
            "status, duration_ms, started_at, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [check_id, run_id, gate_name, check_name, status, duration_ms, _now(), details],
        )
        return check_id

    def record_error(self, check_id: str, error_class: str,
                     file: Optional[str] = None, line_no: Optional[int] = None,
                     expected: Optional[str] = None, actual: Optional[str] = None,
                     remediation: Optional[str] = None,
                     canary_context: Optional[dict] = None) -> tuple[str, bool]:
        """Returns (error_id, is_novel). Dedupes by signature."""
        sig_input = f"{error_class}|{file or ''}|{line_no or ''}"
        signature = hashlib.sha256(sig_input.encode()).hexdigest()[:16]

        existing = self.con.execute(
            "SELECT error_id, occurrence_count FROM gate_errors WHERE signature = ?",
            [signature]
        ).fetchone()

        if existing:
            error_id, count = existing
            self.con.execute(
                "UPDATE gate_errors SET last_seen_at = ?, occurrence_count = ?, is_novel = false "
                "WHERE error_id = ?",
                [_now(), count + 1, error_id],
            )
            return error_id, False

        error_id = _uuid("err_")
        self.con.execute(
            "INSERT INTO gate_errors (error_id, check_id, signature, error_class, is_novel, "
            "file, line_no, expected, actual, remediation, canary_context, "
            "first_seen_at, last_seen_at, occurrence_count) "
            "VALUES (?, ?, ?, ?, true, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            [error_id, check_id, signature, error_class, file, line_no,
             expected, actual, remediation,
             json.dumps(canary_context) if canary_context else None,
             _now(), _now()],
        )
        return error_id, True

    def capture_daemon_state(self, run_id: str):
        """Snapshot per-daemon heartbeat state at gate start."""
        cfg_rows = self.con.execute(
            "SELECT daemon_name, expected_cycle_sec, heartbeat_grace_sec "
            "FROM daemon_cycle_config"
        ).fetchall()

        for daemon, expected, grace in cfg_rows:
            try:
                hb_rows = ws_query(
                    "SELECT last_heartbeat, "
                    "CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age "
                    "FROM service_health WHERE service = ?",
                    params=[daemon],
                )
                if hb_rows:
                    hb = hb_rows[0].get("last_heartbeat")
                    age = hb_rows[0].get("age")
                    within = age < (expected + grace) if expected > 0 else None
                else:
                    hb = None; age = None; within = None
            except Exception:
                hb = None; age = None; within = None

            self.con.execute(
                "INSERT OR REPLACE INTO daemon_state_at_gate "
                "(run_id, daemon_name, last_heartbeat, heartbeat_age_sec, "
                "expected_cycle_sec, heartbeat_grace_sec, is_within_cycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, daemon, hb, age, expected, grace, within],
            )

    def memorialize_canary(self, run_id: str, spec: dict, observed_by: list,
                           final_state: dict, cleanup_ok: bool):
        self.con.execute(
            "INSERT OR REPLACE INTO canary_history "
            "(run_id, canary_spec, observed_by, final_state, cleanup_ok) "
            "VALUES (?, ?, ?, ?, ?)",
            [run_id, json.dumps(spec), json.dumps(observed_by),
             json.dumps(final_state), cleanup_ok],
        )


# =============================================================================
# Gate base class
# =============================================================================

class Gate:
    name: str = "unnamed_gate"

    def __init__(self, db: GateErrorDB, run_id: str):
        self.db = db
        self.run_id = run_id
        self.failures = 0
        self.checks = 0

    def check(self, check_name: str, condition: bool,
              error_class: str = "assertion_failed",
              expected: str = "", actual: str = "",
              file: Optional[str] = None, line_no: Optional[int] = None,
              remediation: str = "") -> bool:
        """Record a single check outcome. Returns the boolean.

        Sleeps INTER_CHECK_MS after recording so the next check gives
        write_service time to settle any queued writes from this check.
        """
        self.checks += 1
        started_at = time.monotonic()
        status = "pass" if condition else "fail"
        details = f"expected={expected!r} actual={actual!r}" if not condition else ""

        check_id = self.db.record_check(
            self.run_id, self.name, check_name, status,
            duration_ms=0, details=details
        )
        if not condition:
            self.failures += 1
            _, is_novel = self.db.record_error(
                check_id, error_class,
                file=file, line_no=line_no,
                expected=expected, actual=actual,
                remediation=remediation,
            )
            marker = "NOVEL" if is_novel else "known"
            print(f"    [FAIL] {check_name}  [{error_class}] ({marker})")
            if expected:
                print(f"           expected: {expected}")
            if actual:
                print(f"           actual:   {actual}")
            if remediation:
                print(f"           fix:      {remediation}")
        else:
            print(f"    [ OK ] {check_name}")

        # Rate-limit inter-check spacing so write_service isn't hammered
        time.sleep(INTER_CHECK_MS / 1000)
        return condition

    def run(self) -> None:
        raise NotImplementedError


# =============================================================================
# Orchestrator
# =============================================================================

@contextmanager
def gate_run(trigger: str = "manual", host_state: str = "unknown"):
    """Context manager around a single gate run. Opens the error DB
    (with lock retry + schema verification), records start/end, captures
    daemon state, always closes cleanly even on exception."""
    db = GateErrorDB()
    run_id = db.open_run(trigger=trigger)
    started = time.monotonic()
    print(f"\n=== Gate run {run_id}  trigger={trigger} ===")
    db.capture_daemon_state(run_id)
    try:
        yield db, run_id
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            db.close_run(run_id, duration_ms, host_state)
        except Exception as e:
            print(f"  [!] close_run failed: {e}")
        print(f"\n=== Gate run {run_id} complete in {duration_ms}ms ===")
        db.close()