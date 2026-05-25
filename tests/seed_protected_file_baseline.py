#!/usr/bin/env python3
"""
seed_protected_file_baseline.py -- One-time additive bootstrap for protected
file integrity tracking.

Adds:
    - protected_file_baseline table in gate_errors.db
    - 2 new taxonomy classes: protected_file_mutated, protected_file_missing

All idempotent. Safe to re-run anytime. Uses lock-retry matching
gate_framework so it cooperates with live gate runs.
"""
import duckdb
import sys
import time

DB = "/home/workspace/gate_errors.db"
RETRIES = 5
BACKOFF = 1.5

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS protected_file_baseline (
        path           VARCHAR PRIMARY KEY,
        sha256         VARCHAR NOT NULL,
        mtime          TIMESTAMPTZ NOT NULL,
        size_bytes     BIGINT NOT NULL,
        baselined_at   TIMESTAMPTZ DEFAULT now(),
        baselined_by   VARCHAR,
        reason         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pfb_mtime ON protected_file_baseline(mtime DESC)",
]

TAXONOMY_ADDITIONS = [
    ("protected_file_mutated",
     "A file listed as PROTECTED in directive_generator has changed content since "
     "its last baselined hash. Could be a legitimate hand-edit (rebaseline to "
     "acknowledge) or an unexpected mutation by the builder or another process.",
     "high", False,
     "Review diff vs backup; if change is intentional, re-baseline with "
     "rebaseline_protected_files.py; if not, restore from .bak and investigate"),
    ("protected_file_missing",
     "A file listed as PROTECTED no longer exists on disk.",
     "critical", False,
     "Restore file from most recent .bak in same directory, or from git; "
     "investigate what removed it"),
    ("protected_file_baselined",
     "First-time observation of a protected file. Baseline recorded; future "
     "changes will fire protected_file_mutated.",
     "low", True,
     "No action required; informational"),
]


def connect():
    for i in range(RETRIES):
        try:
            return duckdb.connect(DB)
        except duckdb.IOException as e:
            if "lock" in str(e).lower() and i < RETRIES - 1:
                time.sleep(BACKOFF * (i + 1))
                continue
            raise
    raise RuntimeError(f"could not acquire {DB} lock")


def main():
    con = connect()
    try:
        for stmt in SCHEMA:
            con.execute(stmt.strip())
        print(f"[OK] {len(SCHEMA)} schema statements applied")

        # Additive taxonomy via LEFT JOIN anti-insert
        con.execute(
            "CREATE TEMP TABLE _seed_pf (class_name VARCHAR, description TEXT, "
            "severity VARCHAR, auto_fixable BOOLEAN, example_fix TEXT)"
        )
        con.executemany(
            "INSERT INTO _seed_pf VALUES (?, ?, ?, ?, ?)",
            TAXONOMY_ADDITIONS,
        )
        added = con.execute("""
            INSERT INTO error_taxonomy
                (class_name, description, severity, auto_fixable, example_fix)
            SELECT s.class_name, s.description, s.severity, s.auto_fixable, s.example_fix
            FROM _seed_pf s
            LEFT JOIN error_taxonomy t ON t.class_name = s.class_name
            WHERE t.class_name IS NULL
            RETURNING class_name
        """).fetchall()
        con.execute("DROP TABLE _seed_pf")

        if added:
            print(f"[OK] added {len(added)} new taxonomy entries: "
                  f"{[r[0] for r in added]}")
        else:
            print("[OK] taxonomy already complete (no additions)")

        # Verify table exists
        tbl_rows = con.execute(
            "SELECT COUNT(*) FROM protected_file_baseline"
        ).fetchone()
        print(f"[OK] protected_file_baseline ready ({tbl_rows[0]} rows)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())