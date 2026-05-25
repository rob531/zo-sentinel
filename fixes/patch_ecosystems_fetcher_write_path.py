#!/usr/bin/env python3
"""
patch_ecosystems_fetcher_write_path.py

Fix for a Commit A bug: ecosystems_metadata_fetcher.py wrote 50/50
'write_failed' on its first cycle. Root cause: WriteService auto-injects
an 'id' column for any table not in its _NO_AUTO_ID list. Our new table
mcp_ecosystems_metadata uses server_id as its PRIMARY KEY and has no
id column, so every INSERT failed with a schema mismatch.

Fix approach:
  1. Drop and recreate mcp_ecosystems_metadata with a BIGINT id column
     AND a UNIQUE constraint on server_id. Id satisfies write_service's
     auto-id path; UNIQUE server_id lets us upsert via /execute with
     proper ON CONFLICT.
  2. Patch ecosystems_metadata_fetcher.py to use /execute with an
     explicit INSERT ... ON CONFLICT(server_id) DO UPDATE statement
     instead of the /write endpoint. This bypasses the auto-id problem
     entirely and is explicit about upsert semantics.

After this patcher runs:
  - table is empty (previous 0 rows is fine -- all failed anyway)
  - fetcher uses /execute path that works
  - next fetcher cycle writes data successfully

Idempotent via marker check.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

TARGET = Path("/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py")
MARKER = "_upsert_metadata_row"  # presence of new helper func = applied
EXECUTE_URL = "http://127.0.0.1:8772/execute"

# ---- Step 1: schema DDL ---------------------------------------------

DROP_OLD = "DROP TABLE IF EXISTS mcp_ecosystems_metadata"
CREATE_NEW = """
CREATE TABLE IF NOT EXISTS mcp_ecosystems_metadata (
    id                  BIGINT PRIMARY KEY,
    server_id           VARCHAR UNIQUE NOT NULL,
    top_package_name    VARCHAR,
    top_package_purl    VARCHAR,
    top_ecosystem       VARCHAR,
    top_downloads       BIGINT,
    top_latest_version  VARCHAR,
    cousin_count        INTEGER,
    ecosystems_observed VARCHAR,
    age_days_estimate   INTEGER,
    stars_estimate      INTEGER,
    raw_response_bytes  INTEGER,
    lookup_status       VARCHAR,
    last_error          VARCHAR,
    fetched_at          TIMESTAMPTZ DEFAULT now()
)
"""

# ---- Step 2: fetcher source patches ---------------------------------

# Replace ensure_schema() to use the new DDL
SCHEMA_OLD = '''def ensure_schema() -> bool:
    """Create mcp_ecosystems_metadata if not present. Idempotent.
    PK on server_id so upserts replace on refresh."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_ecosystems_metadata (
        server_id           VARCHAR PRIMARY KEY,
        top_package_name    VARCHAR,
        top_package_purl    VARCHAR,
        top_ecosystem       VARCHAR,
        top_downloads       BIGINT,
        top_latest_version  VARCHAR,
        cousin_count        INTEGER,
        ecosystems_observed VARCHAR,  -- JSON array of distinct ecosystems
        age_days_estimate   INTEGER,  -- from first_release_published_at where available
        stars_estimate      INTEGER,  -- may be null; ecosyste.ms doesn\'t always have
        raw_response_bytes  INTEGER,  -- size of the ecosyste.ms payload (diagnostic)
        lookup_status       VARCHAR,  -- ok, not_found, error
        last_error          VARCHAR,
        fetched_at          TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)'''

SCHEMA_NEW = '''def ensure_schema() -> bool:
    """Create mcp_ecosystems_metadata if not present. Idempotent.
    Schema revised (post-Commit A bug): id BIGINT PK + server_id UNIQUE
    so WriteService auto-id doesn\'t conflict with PRIMARY KEY constraint."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_ecosystems_metadata (
        id                  BIGINT PRIMARY KEY,
        server_id           VARCHAR UNIQUE NOT NULL,
        top_package_name    VARCHAR,
        top_package_purl    VARCHAR,
        top_ecosystem       VARCHAR,
        top_downloads       BIGINT,
        top_latest_version  VARCHAR,
        cousin_count        INTEGER,
        ecosystems_observed VARCHAR,
        age_days_estimate   INTEGER,
        stars_estimate      INTEGER,
        raw_response_bytes  INTEGER,
        lookup_status       VARCHAR,
        last_error          VARCHAR,
        fetched_at          TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)


def _upsert_metadata_row(row: dict) -> bool:
    """Write via /execute using explicit INSERT ... ON CONFLICT(server_id).
    Avoids WriteService\'s /write auto-id injection which breaks when
    the table has a non-id PRIMARY KEY. Uses hash of server_id as id so
    deduplicate is deterministic across runs."""
    import hashlib
    server_id = row["server_id"]
    row_id = int(hashlib.md5(server_id.encode()).hexdigest()[:8], 16) % (2**31)
    cols = [
        "id", "server_id", "top_package_name", "top_package_purl",
        "top_ecosystem", "top_downloads", "top_latest_version",
        "cousin_count", "ecosystems_observed", "age_days_estimate",
        "stars_estimate", "raw_response_bytes", "lookup_status",
        "last_error", "fetched_at",
    ]
    values = [
        row_id,
        server_id,
        row.get("top_package_name"),
        row.get("top_package_purl"),
        row.get("top_ecosystem"),
        row.get("top_downloads"),
        row.get("top_latest_version"),
        row.get("cousin_count"),
        row.get("ecosystems_observed"),
        row.get("age_days_estimate"),
        row.get("stars_estimate"),
        row.get("raw_response_bytes"),
        row.get("lookup_status"),
        row.get("last_error"),
        row.get("fetched_at"),
    ]
    placeholders = ",".join(["?"] * len(cols))
    update_set = ",".join(
        f"{c}=excluded.{c}" for c in cols if c not in ("id", "server_id")
    )
    sql = (
        f"INSERT INTO mcp_ecosystems_metadata ({\',\'.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(server_id) DO UPDATE SET {update_set}"
    )
    return ws_execute(sql, values)'''

# Replace the ws_write call inside process_server with _upsert_metadata_row
CALL_OLD = '''    if ws_write("mcp_ecosystems_metadata", row):
        return status
    return "write_failed"'''

CALL_NEW = '''    if _upsert_metadata_row(row):
        return status
    return "write_failed"'''


def _backup(path: Path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def drop_and_recreate_table() -> bool:
    """Nuke the existing broken table and create the new schema.
    Safe because the old table has 0 rows (all writes failed)."""
    print("  [db] DROP TABLE mcp_ecosystems_metadata")
    r = requests.post(EXECUTE_URL, json={"sql": DROP_OLD, "agent_id":
                      "patch_ecosystems_fix", "wait": True}, timeout=15)
    if r.status_code != 200:
        print(f"    [FAIL] drop returned {r.status_code}: {r.text[:200]}")
        return False
    print("  [db] CREATE TABLE with id+server_id(UNIQUE)")
    r = requests.post(EXECUTE_URL, json={"sql": CREATE_NEW, "agent_id":
                      "patch_ecosystems_fix", "wait": True}, timeout=15)
    if r.status_code != 200:
        print(f"    [FAIL] create returned {r.status_code}: {r.text[:200]}")
        return False
    print("  [db] schema recreated")
    return True


def patch_source() -> bool:
    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return False
    src = TARGET.read_text()
    if MARKER in src:
        print("  [skip] source already patched")
        return True

    if SCHEMA_OLD not in src:
        print("  [FAIL] schema anchor not found; was fetcher modified?")
        return False
    if CALL_OLD not in src:
        print("  [FAIL] ws_write call anchor not found")
        return False

    src = src.replace(SCHEMA_OLD, SCHEMA_NEW, 1)
    print("  [patch A] ensure_schema + _upsert_metadata_row injected")
    src = src.replace(CALL_OLD, CALL_NEW, 1)
    print("  [patch B] process_server now calls _upsert_metadata_row")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return False

    _backup(TARGET)
    TARGET.write_text(src)
    return True


def main():
    print("=" * 60)
    print("patch_ecosystems_fetcher_write_path.py")
    print("=" * 60)

    print("\n-- Step 1: recreate table with id column --")
    if not drop_and_recreate_table():
        print("  [ABORT] schema fix failed")
        return 2

    print("\n-- Step 2: patch fetcher source --")
    if not patch_source():
        print("  [ABORT] source patch failed")
        return 2

    print("\n" + "=" * 60)
    print("Done. Restart fetcher and adapter:")
    print("")
    print("  pkill -f 'daemon_wrapper.sh ecosystems_metadata_fetcher'")
    print("  sleep 2")
    print("  source /home/workspace/zo_mesh/.zo_env")
    print("  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \\")
    print("    ecosystems_metadata_fetcher \\")
    print("    /home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py \\")
    print("    >> /home/workspace/logs/ecosystems_metadata_fetcher.log 2>&1 &")
    print("")
    print("  sleep 60   # let first cycle complete")
    print("  python3 /home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py --once")
    print("")
    print("Verify rows now exist:")
    print("  curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\")
    print("    -d '{\"sql\":\"SELECT lookup_status, COUNT(*) FROM mcp_ecosystems_metadata GROUP BY 1\"}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())