#!/usr/bin/env python3
"""
gate_errors_bootstrap.py -- Creates the standalone gate_errors.db.

Peer to full_schema_bootstrap.py. Run via python3 after the main bootstrap
on every boot, or any time the gate schema may have drifted. Idempotent.

Concurrency notes:
    - DuckDB locks the file for writes. If a gate run is in progress, this
      bootstrap waits (lock retry with backoff) rather than crashing.
    - All DDL uses IF NOT EXISTS so a running gate's data is preserved.
    - Seed inserts use LEFT JOIN anti-pattern so re-runs don't duplicate rows.

Usage:
    python3 /home/workspace/zo_sentinel/tests/gate_errors_bootstrap.py

Exit codes:
    0 -- table exists (created or verified)
    1 -- failure (lock not acquired, schema statement failed)

Design doc: ENRICHMENT_STAGING.md (staging discussion), gate_framework.py (runtime)
"""
import duckdb
import sys
import time

DB_PATH = "/home/workspace/gate_errors.db"

# Lock-retry parameters -- match gate_framework for consistency
DB_LOCK_MAX_RETRIES = 5
DB_LOCK_BACKOFF_SEC = 1.5


def _connect_with_retry(path: str) -> duckdb.DuckDBPyConnection:
    """Open DB, retrying if another process holds the write lock.
    Matches the retry profile in gate_framework.py so neither side
    prematurely gives up on the other."""
    last_err = None
    for attempt in range(DB_LOCK_MAX_RETRIES):
        try:
            return duckdb.connect(path)
        except duckdb.IOException as e:
            last_err = e
            msg = str(e).lower()
            if "lock" in msg or "in use" in msg:
                if attempt < DB_LOCK_MAX_RETRIES - 1:
                    wait = DB_LOCK_BACKOFF_SEC * (attempt + 1)
                    print(f"[WAIT] {path} locked, retrying in {wait:.1f}s "
                          f"(attempt {attempt+1}/{DB_LOCK_MAX_RETRIES})")
                    time.sleep(wait)
                    continue
            raise
    raise RuntimeError(
        f"Could not acquire {path} lock after {DB_LOCK_MAX_RETRIES} attempts: "
        f"{last_err}. A gate run or reader may still hold the connection."
    )


SCHEMA = [
    # ---------- gate_runs ----------
    """
    CREATE TABLE IF NOT EXISTS gate_runs (
        run_id          VARCHAR PRIMARY KEY,
        started_at      TIMESTAMPTZ NOT NULL,
        finished_at     TIMESTAMPTZ,
        trigger         VARCHAR,
        gates_planned   INTEGER DEFAULT 0,
        gates_passed    INTEGER DEFAULT 0,
        gates_failed    INTEGER DEFAULT 0,
        duration_ms     INTEGER,
        host_state      VARCHAR,
        notes           TEXT
    )
    """,
    # ---------- gate_checks ----------
    """
    CREATE TABLE IF NOT EXISTS gate_checks (
        check_id        VARCHAR PRIMARY KEY,
        run_id          VARCHAR NOT NULL,
        gate_name       VARCHAR NOT NULL,
        check_name      VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        duration_ms     INTEGER,
        started_at      TIMESTAMPTZ,
        details         TEXT
    )
    """,
    # ---------- gate_errors ----------
    """
    CREATE TABLE IF NOT EXISTS gate_errors (
        error_id         VARCHAR PRIMARY KEY,
        check_id         VARCHAR NOT NULL,
        signature        VARCHAR NOT NULL UNIQUE,
        error_class      VARCHAR NOT NULL,
        is_novel         BOOLEAN DEFAULT true,
        file             VARCHAR,
        line_no          INTEGER,
        expected         TEXT,
        actual           TEXT,
        remediation      TEXT,
        canary_context   TEXT,
        first_seen_at    TIMESTAMPTZ NOT NULL,
        last_seen_at     TIMESTAMPTZ NOT NULL,
        occurrence_count INTEGER DEFAULT 1
    )
    """,
    # ---------- error_taxonomy ----------
    """
    CREATE TABLE IF NOT EXISTS error_taxonomy (
        class_name            VARCHAR PRIMARY KEY,
        description           TEXT NOT NULL,
        severity              VARCHAR NOT NULL,
        auto_fixable          BOOLEAN DEFAULT false,
        example_fix           TEXT,
        first_catalogued_at   TIMESTAMPTZ DEFAULT now()
    )
    """,
    # ---------- canary_history ----------
    """
    CREATE TABLE IF NOT EXISTS canary_history (
        run_id          VARCHAR PRIMARY KEY,
        canary_spec     TEXT NOT NULL,
        observed_by     TEXT,
        final_state     TEXT,
        cleanup_ok      BOOLEAN DEFAULT false,
        captured_at     TIMESTAMPTZ DEFAULT now()
    )
    """,
    # ---------- daemon_state_at_gate ----------
    """
    CREATE TABLE IF NOT EXISTS daemon_state_at_gate (
        run_id                       VARCHAR NOT NULL,
        daemon_name                  VARCHAR NOT NULL,
        last_heartbeat               TIMESTAMPTZ,
        heartbeat_age_sec            INTEGER,
        rows_written_since_last_gate INTEGER,
        expected_cycle_sec           INTEGER,
        heartbeat_grace_sec          INTEGER,
        is_within_cycle              BOOLEAN,
        captured_at                  TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (run_id, daemon_name)
    )
    """,
    # ---------- daemon_cycle_config ----------
    """
    CREATE TABLE IF NOT EXISTS daemon_cycle_config (
        daemon_name          VARCHAR PRIMARY KEY,
        expected_cycle_sec   INTEGER NOT NULL,
        heartbeat_grace_sec  INTEGER NOT NULL,
        notes                TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gate_checks_run ON gate_checks(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_gate_errors_check ON gate_errors(check_id)",
    "CREATE INDEX IF NOT EXISTS idx_gate_errors_last_seen ON gate_errors(last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_gate_runs_started ON gate_runs(started_at DESC)",
]

SEED_TAXONOMY = [
    ("port_mismatch",
     "URL points to an unexpected port (e.g. :8773 when write_service is :8772)",
     "critical", True,
     "patch_attestation_engine.sh pattern"),
    ("endpoint_semantic_mismatch",
     "SELECT sent to /execute (fire-and-forget, discards rows) or DML sent to /query",
     "critical", True,
     "patch_trust_synthesiser_endpoint.sh pattern"),
    ("missing_pk_constraint",
     "INSERT ... ON CONFLICT used but table has no matching UNIQUE or PRIMARY KEY",
     "critical", True,
     "patch_missing_pk_constraints.sh pattern"),
    ("payload_key_drift",
     "ws_write dict key does not match target table column (e.g. tool_name vs server_id)",
     "critical", True,
     "patch_trust_synthesiser_server_id.sh pattern"),
    ("null_source_dedup_hole",
     "UNIQUE constraint includes a source column but writer does not populate it",
     "medium", True,
     "Add source='<daemon_name>' to every ws_write call"),
    ("stale_schema_ref",
     "SELECT names a column or table that does not exist in the live schema",
     "critical", False,
     "Manual: verify table exists and column names match"),
    ("race_prone_id_gen",
     "SELECT MAX(id)+1 pattern detected; loses rows under concurrent writes",
     "low", True,
     "Replace with DEFAULT nextval('seq_xxx')"),
    ("heartbeat_missing",
     "Daemon runs but never writes to service_health; invisible to monitoring",
     "medium", True,
     "Add send_heartbeat() call to main loop"),
    ("legacy_pidfile_pattern",
     "Uses /var/run/zo/*.pid or similar patterns that do not survive reboots",
     "low", True,
     "Replace with fcntl.flock(/tmp/<n>.lock)"),
    # Gate-specific errors added 2026-04-17
    ("infra_unreachable",
     "Expected infrastructure endpoint (write_service, duckdb_constraints) not reachable",
     "high", False,
     "Check write_service is running and healthy"),
    ("endpoint_response_shape",
     "Endpoint returned an unexpected response shape (e.g. /query with no 'rows' key)",
     "critical", False,
     "Check write_service version; may indicate API contract change"),
    ("canary_insert_failed",
     "Gate could not write a canary row to a production table",
     "high", False,
     "Usually a cascading effect -- look for PK/payload/endpoint errors first"),
    ("canary_read_failed",
     "Canary row was written but could not be read back",
     "high", False,
     "Indicates eventual-consistency failure or broken /query endpoint"),
    ("canary_cleanup_failed",
     "Gate left canary data in production tables after run ended",
     "high", False,
     "Manually DELETE rows matching __gate_canary_ prefix from affected tables"),
    ("pivot_sql_failed",
     "trust_synthesiser's pivot SQL raises or returns wrong cardinality",
     "critical", False,
     "Re-check trust_synthesiser.query_signal_scores SQL and endpoint"),
    ("pivot_sql_wrong_score",
     "Pivot row has wrong score for a signal (schema drift or write failure)",
     "high", False,
     "Look for recent changes to mcp_signal_scores schema"),
    ("composite_math_wrong",
     "Gate 5's composite computation diverges from trust_synthesiser's WEIGHTS",
     "medium", True,
     "Sync WEIGHTS between gate_5_synthesis_flow.py and trust_synthesiser.py"),
]

SEED_CYCLE_CONFIG = [
    ("mcp_scanner", 21600, 7200,
     "6h cycle, 2h grace -- scans all sources"),
    ("signal_analyser", 1800, 600,
     "30min cycle, 10min grace"),
    ("trust_synthesiser", 1800, 600,
     "30min cycle, 10min grace"),
    ("threat_intel_ingestor", 7200, 1800,
     "2h cycle, 30min grace"),
    ("risk_ranker", 14400, 3600,
     "4h cycle, 1h grace"),
    ("attestation_engine", 21600, 7200,
     "6h cycle, 2h grace"),
    ("registry_api", 0, 0,
     "FastAPI, no cycle-based heartbeat expected"),
    ("write_service", 60, 180,
     "Heartbeats every 60s as part of write loop"),
    ("inference_router", 60, 180,
     "Same heartbeat pattern as write_service"),
    ("manager_agent", 30, 90,
     "30s polling loop"),
    ("sentinel_directive_generator", 3600, 900,
     "1h cycle, 15min grace"),
]


def bootstrap(db_path: str = DB_PATH) -> int:
    from pathlib import Path
    is_new = not Path(db_path).exists()

    try:
        con = _connect_with_retry(db_path)
    except Exception as e:
        print(f"[FAIL] Cannot open DuckDB at {db_path}: {e}")
        return 1

    try:
        for stmt in SCHEMA:
            try:
                con.execute(stmt.strip())
            except Exception as e:
                print(f"[FAIL] Schema stmt failed: {e}")
                print(f"  Stmt: {stmt.strip()[:120]}")
                return 1
        print(f"[OK] {len(SCHEMA)} schema statements applied")

        # Seed taxonomy -- idempotent via anti-join
        con.execute(
            "CREATE TEMP TABLE _seed_tax (class_name VARCHAR, description TEXT, "
            "severity VARCHAR, auto_fixable BOOLEAN, example_fix TEXT)"
        )
        con.executemany(
            "INSERT INTO _seed_tax VALUES (?, ?, ?, ?, ?)",
            SEED_TAXONOMY,
        )
        con.execute("""
            INSERT INTO error_taxonomy
                (class_name, description, severity, auto_fixable, example_fix)
            SELECT s.class_name, s.description, s.severity, s.auto_fixable, s.example_fix
            FROM _seed_tax s
            LEFT JOIN error_taxonomy t ON t.class_name = s.class_name
            WHERE t.class_name IS NULL
        """)
        con.execute("DROP TABLE _seed_tax")

        # Seed cycle config
        con.execute(
            "CREATE TEMP TABLE _seed_cyc (daemon_name VARCHAR, "
            "expected_cycle_sec INTEGER, heartbeat_grace_sec INTEGER, notes TEXT)"
        )
        con.executemany(
            "INSERT INTO _seed_cyc VALUES (?, ?, ?, ?)",
            SEED_CYCLE_CONFIG,
        )
        con.execute("""
            INSERT INTO daemon_cycle_config
                (daemon_name, expected_cycle_sec, heartbeat_grace_sec, notes)
            SELECT s.daemon_name, s.expected_cycle_sec, s.heartbeat_grace_sec, s.notes
            FROM _seed_cyc s
            LEFT JOIN daemon_cycle_config c ON c.daemon_name = s.daemon_name
            WHERE c.daemon_name IS NULL
        """)
        con.execute("DROP TABLE _seed_cyc")

        # Verify
        tax_n = con.execute("SELECT COUNT(*) FROM error_taxonomy").fetchone()[0]
        cyc_n = con.execute("SELECT COUNT(*) FROM daemon_cycle_config").fetchone()[0]
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()]
    except Exception as e:
        print(f"[FAIL] Seeding or verification: {e}")
        return 1
    finally:
        con.close()

    marker = "created" if is_new else "verified"
    print(f"[OK] gate_errors.db {marker} at {db_path}")
    print(f"     tables: {len(tables)} ({', '.join(tables)})")
    print(f"     error_taxonomy: {tax_n} classes")
    print(f"     daemon_cycle_config: {cyc_n} daemons")
    return 0


if __name__ == "__main__":
    sys.exit(bootstrap())