#!/usr/bin/env python3
"""
rebaseline_protected_files.py -- Explicitly acknowledge protected file changes.

Workflow:
    1. Run gates; Gate 2 fires protected_file_mutated for file X
    2. You review the diff vs X.bak.<ts>
    3. If the change is intentional, run:
           python3 rebaseline_protected_files.py X
       or for all protected files:
           python3 rebaseline_protected_files.py --all
    4. Next gate run records the new hash as baseline; no more alert

Usage:
    python3 rebaseline_protected_files.py <filename>       # one file
    python3 rebaseline_protected_files.py --all            # all PROTECTED_FILES
    python3 rebaseline_protected_files.py --list           # show current baselines
    python3 rebaseline_protected_files.py --stale          # show drift without re-baselining
"""
import duckdb
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB = "/home/workspace/gate_errors.db"
SENTINEL = Path("/home/workspace/zo_sentinel")
RETRIES = 5
BACKOFF = 1.5

# Must stay in sync with sentinel_directive_generator.PROTECTED_FILES
PROTECTED_FILES = [
    ('signal_analyser.py', '/home/workspace/zo_sentinel/signal_analyser.py'),
    ('trust_synthesiser.py', '/home/workspace/zo_sentinel/trust_synthesiser.py'),
    ('write_service.py', '/home/workspace/zo_mesh/write_service.py'),
    ('inference_router_service.py', '/home/workspace/zo_mesh/inference_router_service.py'),
    ('full_schema_bootstrap.py', '/home/workspace/zo_sentinel/full_schema_bootstrap.py'),
    ('mcp_scanner.py', '/home/workspace/zo_sentinel/mcp_scanner.py'),
    ('registry_api.py', '/home/workspace/zo_sentinel/registry_api.py'),
    ('attestation_engine.py', '/home/workspace/zo_sentinel/attestation_engine.py'),
    ('threat_intel_ingestor.py', '/home/workspace/zo_sentinel/threat_intel_ingestor.py'),
    ('rug_pull_monitor.py', '/home/workspace/zo_sentinel/rug_pull_monitor.py'),
    ('ui_server.py', '/home/workspace/zo_sentinel/ui_server.py'),
    ('dashboard.html', '/home/workspace/zo_sentinel/dashboard.html'),
    ('sentinel_status.html', '/home/workspace/zo_sentinel/sentinel_status.html'),
    ('approval_workflow.py', '/home/workspace/zo_sentinel/approval_workflow.py'),
    ('search_api.py', '/home/workspace/zo_sentinel/search_api.py'),
    ('dashboard_api.py', '/home/workspace/zo_sentinel/dashboard_api.py'),
    ('forensic_detail_api.py', '/home/workspace/zo_sentinel/forensic_detail_api.py'),
    ('comparison_api.py', '/home/workspace/zo_sentinel/comparison_api.py'),
    ('advanced_filter_api.py', '/home/workspace/zo_sentinel/advanced_filter_api.py'),
    ('manual_override_api.py', '/home/workspace/zo_sentinel/manual_override_api.py'),
    ('bulk_assess_api.py', '/home/workspace/zo_sentinel/bulk_assess_api.py'),
]
# Map display_name -> absolute path for lookups
PROTECTED_PATHS = {name: abs_path for name, abs_path in PROTECTED_FILES}



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


def file_fingerprint(path: Path) -> tuple[str, datetime, int] | None:
    """Return (sha256, mtime_utc, size_bytes) or None if missing."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return h.hexdigest(), mtime, stat.st_size


def rebaseline_one(con, name: str, reason: str = "", baselined_by: str = "cli") -> bool:
    abs_path = PROTECTED_PATHS.get(name, str(SENTINEL / name))
    path = Path(abs_path)
    fp = file_fingerprint(path)
    if fp is None:
        print(f"  [MISS] {name} does not exist")
        return False
    sha, mtime, size = fp

    # Check if baseline exists for comparison output
    prior = con.execute(
        "SELECT sha256 FROM protected_file_baseline WHERE path = ?",
        [name]
    ).fetchone()

    con.execute(
        "INSERT OR REPLACE INTO protected_file_baseline "
        "(path, sha256, mtime, size_bytes, baselined_at, baselined_by, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [name, sha, mtime, size, datetime.now(timezone.utc), baselined_by, reason],
    )
    if prior and prior[0] != sha:
        print(f"  [UPDATE] {name}  {prior[0][:12]} -> {sha[:12]}  size={size}")
    elif prior:
        print(f"  [NOOP]   {name}  unchanged {sha[:12]}")
    else:
        print(f"  [NEW]    {name}  baselined {sha[:12]}  size={size}")
    return True


def cmd_list(con):
    rows = con.execute(
        "SELECT path, SUBSTR(sha256, 1, 12) AS sha, size_bytes, "
        "baselined_at, COALESCE(baselined_by, '-') AS by_ "
        "FROM protected_file_baseline ORDER BY path"
    ).fetchall()
    if not rows:
        print("(no baselines yet -- first gate run will create them)")
        return
    print(f"{'path':<42} {'sha':<14} {'size':>10}  baselined_at")
    print("-" * 90)
    for path, sha, size, when, by_ in rows:
        print(f"{path:<42} {sha:<14} {size:>10}  {when}  ({by_})")


def cmd_stale(con):
    """Show files whose current disk state differs from their baseline."""
    print("Checking all PROTECTED_FILES against stored baselines...")
    drift = 0
    for name, _abs in PROTECTED_FILES:
        path = SENTINEL / name
        fp = file_fingerprint(path)
        baseline = con.execute(
            "SELECT sha256 FROM protected_file_baseline WHERE path = ?",
            [name]
        ).fetchone()
        if fp is None:
            if baseline:
                print(f"  [MISSING]  {name}  baselined but now absent")
                drift += 1
            continue
        sha, _, _ = fp
        if baseline is None:
            print(f"  [NEW]      {name}  no baseline yet")
            drift += 1
            continue
        if baseline[0] != sha:
            print(f"  [MUTATED]  {name}  disk={sha[:12]}  baseline={baseline[0][:12]}")
            drift += 1
    if drift == 0:
        print("All protected files match their baselines.")
    else:
        print(f"\n{drift} file(s) with drift. Review diffs then re-baseline:")
        print("  python3 rebaseline_protected_files.py --all  (accept all changes)")
        print("  python3 rebaseline_protected_files.py <name>  (accept one change)")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    con = connect()
    try:
        if args[0] == "--list":
            cmd_list(con)
            return 0
        if args[0] == "--stale":
            cmd_stale(con)
            return 0

        if args[0] == "--all":
            reason = args[1] if len(args) > 1 else "bulk rebaseline"
            print(f"Re-baselining all {len(PROTECTED_FILES)} protected files...")
            ok_count = 0
            for name, _abs in PROTECTED_FILES:
                if rebaseline_one(con, name, reason=reason):
                    ok_count += 1
            print(f"\n[OK] {ok_count} of {len(PROTECTED_FILES)} re-baselined")
            return 0

        # Named files
        reason = "cli rebaseline"
        for name in args:
            if name.startswith("--"):
                continue
            if name not in PROTECTED_PATHS:
                print(f"  [WARN] {name} is not in PROTECTED_FILES -- "
                      "baselining anyway")
            rebaseline_one(con, name, reason=reason)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())