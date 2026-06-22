#!/usr/bin/env python3
"""
Diagnostic utility to investigate why mcp_signal_enrichments has only 12 rows
while mcp_signal_scores has 2,044,668 rows.

Performs:
  1. Checks if enrichment modules are actually being called by signal_analyser
  2. Verifies enrichment_harness.py is writing to mcp_signal_enrichments correctly
  3. Checks for any filtering or INSERT failures
  4. Queries information_schema.columns for enrichment table structure
  5. Outputs findings as JSON to stdout
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_CONFIG = {
    "host":     os.environ.get("ZO_DB_HOST", "localhost"),
    "port":     int(os.environ.get("ZO_DB_PORT", "5432")),
    "dbname":   os.environ.get("ZO_DB_NAME", "zo_sentinel"),
    "user":     os.environ.get("ZO_DB_USER", "postgres"),
    "password": os.environ.get("ZO_DB_PASSWORD", ""),
}

ENRICHMENT_TABLE = "mcp_signal_enrichments"
SCORES_TABLE     = "mcp_signal_scores"

# Likely locations of the source tree; adjust as needed.
SEARCH_ROOTS = [
    Path(os.environ.get("ZO_REPO_ROOT", ".")).resolve(),
    Path("/opt/zo-sentinel"),
    Path("/srv/zo-sentinel"),
    Path.home() / "zo-sentinel",
]

# Files we want to inspect for evidence of enrichment wiring.
FILES_OF_INTEREST = [
    "signal_analyser.py",
    "enrichment_harness.py",
    "enrichment/__init__.py",
    "enrichment/base.py",
    "enrichment/registry.py",
]

# Log locations to scan for INSERT/ERROR messages.
LOG_CANDIDATES = [
    Path("/var/log/zo-sentinel/signal_analyser.log"),
    Path("./logs/signal_analyser.log"),
    Path("./signal_analyser.log"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(fn, *args, default=None, **kwargs):
    """Run fn; on any exception return default and capture the error string."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:                                # noqa: BLE001
        return default, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Database diagnostics
# ---------------------------------------------------------------------------

def db_connect():
    """Return a psycopg2 connection or raise."""
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def gather_db_diagnostics():
    """Return a dict of database-side findings."""
    findings = {
        "connection": None,
        "error":      None,
        "tables":     {},
    }

    conn, err = _safe(db_connect)
    if err:
        findings["error"] = err
        return findings

    findings["connection"] = "ok"
    try:
        with conn:
            with conn.cursor() as cur:
                # 1. Row counts.
                for tbl in (ENRICHMENT_TABLE, SCORES_TABLE):
                    cur.execute(
                        "SELECT to_regclass(%s) IS NOT NULL, "
                        "       (SELECT n_live_tup FROM pg_stat_user_tables "
                        "        WHERE relname = %s)",
                        (tbl, tbl),
                    )
                    exists, est_rows = cur.fetchone()
                    findings["tables"][tbl] = {
                        "exists":      bool(exists),
                        "pg_stat_rows": est_rows,
                    }
                    if exists:
                        cur.execute(f'SELECT COUNT(*) FROM "{tbl}"')
                        findings["tables"][tbl]["exact_count"] = cur.fetchone()[0]

                # 2. Column structure of the enrichment table.
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM   information_schema.columns
                    WHERE  table_schema = 'public'
                      AND  table_name   = %s
                    ORDER  BY ordinal_position
                    """,
                    (ENRICHMENT_TABLE,),
                )
                findings["enrichment_columns"] = [
                    {
                        "column_name":    c[0],
                        "data_type":      c[1],
                        "is_nullable":    c[2],
                        "column_default": c[3],
                    }
                    for c in cur.fetchall()
                ]

                # 3. Indexes / primary key (helps explain filtering by score_id).
                cur.execute(
                    """
                    SELECT indexname, indexdef
                    FROM   pg_indexes
                    WHERE  schemaname = 'public' AND tablename = %s
                    """,
                    (ENRICHMENT_TABLE,),
                )
                findings["enrichment_indexes"] = [
                    {"name": i[0], "definition": i[1]} for i in cur.fetchall()
                ]

                # 4. Triggers (silent INSERT failures often come from BEFORE triggers).
                cur.execute(
                    """
                    SELECT trigger_name, event_manipulation, action_timing,
                           action_statement
                    FROM   information_schema.triggers
                    WHERE  event_object_schema = 'public'
                      AND  event_object_table   = %s
                    """,
                    (ENRICHMENT_TABLE,),
                )
                findings["enrichment_triggers"] = [
                    {
                        "name":      t[0],
                        "event":     t[1],
                        "timing":    t[2],
                        "statement": t[3],
                    }
                    for t in cur.fetchall()
                ]

                # 5. Foreign-key wiring between the two tables.
                cur.execute(
                    """
                    SELECT tc.constraint_name, kcu.column_name,
                           ccu.table_name AS foreign_table,
                           ccu.column_name AS foreign_column
                    FROM   information_schema.table_constraints tc
                    JOIN   information_schema.key_column_usage kcu
                           ON tc.constraint_name = kcu.constraint_name
                    JOIN   information_schema.constraint_column_usage ccu
                           ON tc.constraint_name = ccu.constraint_name
                    WHERE  tc.table_schema = 'public'
                      AND  tc.table_name   = %s
                      AND  tc.constraint_type = 'FOREIGN KEY'
                    """,
                    (ENRICHMENT_TABLE,),
                )
                findings["enrichment_foreign_keys"] = [
                    {
                        "name":            fk[0],
                        "column":          fk[1],
                        "references_table": fk[2],
                        "references_column": fk[3],
                    }
                    for fk in cur.fetchall()
                ]

                # 6. Sample rows + id range so we can see whether writes
                #    have stopped, never started, or are heavily filtered.
                cur.execute(
                    f'SELECT MIN(id), MAX(id), COUNT(DISTINCT score_id) '
                    f'FROM "{ENRICHMENT_TABLE}"'
                )
                mn, mx, distinct = cur.fetchone()
                findings["enrichment_stats"] = {
                    "min_id":        mn,
                    "max_id":        mx,
                    "distinct_score_ids": distinct,
                }

                cur.execute(
                    f'SELECT MIN(id), MAX(id) FROM "{SCORES_TABLE}"'
                )
                smin, smax = cur.fetchone()
                findings["scores_stats"] = {
                    "min_id": smin,
                    "max_id": smax,
                }

                # 7. Last few rows of enrichment to see what (if anything)
                #    is being written.
                cur.execute(
                    f'SELECT * FROM "{ENRICHMENT_TABLE}" '
                    f'ORDER BY id DESC LIMIT 5'
                )
                col_names = [d[0] for d in cur.description]
                findings["enrichment_recent_rows"] = [
                    dict(zip(col_names, row)) for row in cur.fetchall()
                ]
    except Exception as exc:                                # noqa: BLE001
        findings["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return findings


# ---------------------------------------------------------------------------
# Source-code diagnostics
# ---------------------------------------------------------------------------

def _find_files():
    found = {}
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for name in FILES_OF_INTEREST:
            for path in root.rglob(name):
                # Skip virtual-env / node_modules noise.
                if any(part in path.parts for part in (".venv", "venv", "node_modules")):
                    continue
                found.setdefault(name, []).append(str(path))
    return found


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:                                # noqa: BLE001
        return f"<<unreadable: {exc}>>"


# Patterns that hint at whether enrichment is wired up.
SIGNAL_ANALYSER_PATTERNS = [
    r"enrichment[_a-zA-Z0-9]*",
    r"from\s+enrichment[\w.]*\s+import",
    r"enrichment_harness",
    r"mcp_signal_enrichments",
    r"INSERT\s+INTO\s+[\"']?mcp_signal_enrichments",
    r"run_enrichment|process_signal|analyse",
]

HARNESS_PATTERNS = [
    r"INSERT\s+INTO\s+[\"']?mcp_signal_enrichments",
    r"ON\s+CONFLICT",
    r"executemany|execute_values|execute_batch",
    r"return\s+False|rollback|except\s+Exception",
    r"score_id",
    r"WHERE\s+1=0|WHERE\s+False",
    r"if\s+not\s+enabled|ENABLED\s*=\s*False|disabled",
]


def _scan(text: str, patterns):
    hits = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE | re.MULTILINE):
            line_no = text[:m.start()].count("\n") + 1
            line = text.splitlines()[line_no - 1].strip() if line_no else ""
            hits.append({"pattern": p, "line": line_no, "match": m.group(0), "context": line[:200]})
    return hits


def gather_code_diagnostics():
    findings = {"files": {}, "errors": []}
    files = _find_files()
    for name, paths in files.items():
        for p in paths:
            text = _read_text(Path(p))
            if text.startswith("<<unreadable"):
                findings["errors"].append({"file": p, "error": text})
                continue
            patterns = (SIGNAL_ANALYSER_PATTERNS if "signal_analyser" in name
                        else HARNESS_PATTERNS if "enrichment_harness" in name
                        else None)
            entry = {
                "path":  p,
                "bytes": len(text),
                "hits":  _scan(text, patterns) if patterns else [],
            }
            findings["files"][name] = findings["files"].get(name, []) + [entry]
    return findings


# ---------------------------------------------------------------------------
# Log-file diagnostics
# ---------------------------------------------------------------------------

def gather_log_diagnostics():
    findings = {"files_scanned": [], "matches": {}}
    needle_re = re.compile(
        r"(mcp_signal_enrichments|INSERT\s+INTO.*enrich|"
        r"enrichment_harness|ERROR|TRACEBACK|ROLLBACK)",
        re.IGNORECASE,
    )
    for log in LOG_CANDIDATES:
        if not log.exists():
            continue
        findings["files_scanned"].append(str(log))
        try:
            with log.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, start=1):
                    if needle_re.search(line):
                        findings["matches"].setdefault(str(log), []).append(
                            {"line_no": i, "text": line.rstrip()[:400]}
                        )
                        if len(findings["matches"][str(log)]) >= 50:
                            break
        except Exception as exc:                            # noqa: BLE001
            findings["matches"][str(log)] = [{"error": str(exc)}]
    return findings


# ---------------------------------------------------------------------------
# Conclusion synthesis
# ---------------------------------------------------------------------------

def synthesise(db, code, logs):
    verdicts = []

    if db.get("error"):
        verdicts.append({
            "check":   "database",
            "status":  "error",
            "detail":  db["error"],
        })
    else:
        etbl = db["tables"].get(ENRICHMENT_TABLE, {})
        stbl = db["tables"].get(SCORES_TABLE, {})
        if not etbl.get("exists"):
            verdicts.append({"check": "table_exists",
                             "status": "fail",
                             "detail": f"{ENRICHMENT_TABLE} does not exist"})
        else:
            ec = etbl.get("exact_count", "?")
            sc = stbl.get("exact_count", "?")
            ratio = f"{ec}/{sc}" if isinstance(ec, int) and isinstance(sc, int) else "n/a"
            verdicts.append({
                "check":  "row_counts",
                "status": "info",
                "detail": f"scores={sc}, enrichments={ec}, ratio={ratio}",
            })

        triggers = db.get("enrichment_triggers") or []
        if triggers:
            verdicts.append({
                "check":  "triggers",
                "status": "warn",
                "detail": f"{len(triggers)} trigger(s) on {ENRICHMENT_TABLE}; "
                          f"BEFORE triggers can silently swallow inserts",
                "triggers": triggers,
            })
        else:
            verdicts.append({"check": "triggers", "status": "ok",
                             "detail": "no triggers on enrichment table"})

    # Code-side verdicts.
    sa_hits = []
    for entries in (code.get("files") or {}).get("signal_analyser.py", []):
        sa_hits += entries.get("hits", [])
    eh_hits = []
    for entries in (code.get("files") or {}).get("enrichment_harness.py", []):
        eh_hits += entries.get("hits", [])

    verdicts.append({
        "check":  "signal_analyser_calls_enrichment",
        "status": "ok" if sa_hits else "warn",
        "detail": (f"found {len(sa_hits)} enrichment-related reference(s) "
                   f"in signal_analyser.py" if sa_hits
                   else "no references to enrichment modules in signal_analyser.py"),
    })
    verdicts.append({
        "check":  "harness_inserts_present",
        "status": "ok" if eh_hits else "fail",
        "detail": (f"found {len(eh_hits)} INSERT/IO reference(s) in "
                   f"enrichment_harness.py" if eh_hits
                   else "enrichment_harness.py appears to never INSERT "
                        "into mcp_signal_enrichments"),
    })

    # Detect a "disabled" or hard-coded "0 rows" filter.
    for entries in (code.get("files") or {}).get("enrichment_harness.py", []):
        for h in entries.get("hits", []):
            ctx = (h.get("context") or "").lower()
            if "where 1=0" in ctx or "where false" in ctx or "disabled" in ctx:
                verdicts.append({
                    "check":  "harness_filtering",
                    "status": "fail",
                    "detail": f"possible filtering / disablement in harness: {h['context']}",
                })
                break

    # Log verdicts.
    for log_path, items in (logs.get("matches") or {}).items():
        errs = [m for m in items if "error" in (m.get("text", "").lower())]
        if errs:
            verdicts.append({
                "check":  "log_errors",
                "status": "warn",
                "detail": f"{len(errs)} ERROR/exception line(s) in {log_path}",
                "sample": errs[:5],
            })

    return verdicts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    report = {
        "generated_at": _now(),
        "target":       ENRICHMENT_TABLE,
        "comparator":   SCORES_TABLE,
        "db":     gather_db_diagnostics(),
        "code":   gather_code_diagnostics(),
        "logs":   gather_log_diagnostics(),
    }
    report["verdicts"] = synthesise(report["db"], report["code"], report["logs"])

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(json.dumps({"interrupted": True}, indent=2))
        sys.exit(130)
    except Exception as exc:                                # noqa: BLE001
        print(json.dumps({
            "fatal_error": f"{type(exc).__name__}: {exc}",
            "traceback":   traceback.format_exc(),
        }, indent=2))
        sys.exit(1)