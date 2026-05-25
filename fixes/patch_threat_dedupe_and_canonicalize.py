#!/usr/bin/env python3
"""
patch_threat_dedupe_and_canonicalize.py

Fixes two data-hygiene problems surfaced by UI audit:

FIX 1 (runtime): threat_intel_ingestor appends OSV threats on every
  2-hour cycle without dedup, producing N duplicates per day per CVE.
  After 2 days, each CVE appears 24 times per server. Patch:
  before writing a threat, check if (server_id, osv_id) already exists
  in mcp_threat_associations. Skip if present. Idempotent.

FIX 2 (one-shot cleanup): remove duplicates from existing
  mcp_threat_associations. For each (server_id, evidence) group,
  keep the oldest row; delete the rest. Does NOT touch distinct CVEs.

FIX 2b (canonicalization): 15 bootstrap entries in mcp_server_registry
  have malformed 16-char server_ids (pattern 'a1b2c3d4e5f600NN'). 11 of
  these have proper 32-char MD5 canonical twins (same name+url). Migrate
  threats + definition_history to the canonical twin, then delete the
  bootstrap registry row. Keep the 4 orphans (no canonical twin) in place.

The migration is idempotent -- re-running is safe because:
  - after migration there are no short-id rows in mcp_threat_associations
  - after migration the 11 bootstrap registry rows are gone
  - the threat_intel_ingestor patch checks before inserting

All destructive ops (DELETE) run via /execute and are logged.

Run modes:
  python3 patch_threat_dedupe_and_canonicalize.py           # apply all
  python3 patch_threat_dedupe_and_canonicalize.py --dry-run  # preview only
  python3 patch_threat_dedupe_and_canonicalize.py --code-only  # ingestor patch only
  python3 patch_threat_dedupe_and_canonicalize.py --data-only  # DB cleanup only
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---- Config -----------------------------------------------------------

THREAT_INGESTOR = Path("/home/workspace/zo_sentinel/threat_intel_ingestor.py")
WRITE_SERVICE   = "http://127.0.0.1:8772"
QUERY_URL       = f"{WRITE_SERVICE}/query"
EXECUTE_URL     = f"{WRITE_SERVICE}/execute"

# Bootstrap server_ids to migrate. Format: (bad_id, canonical_twin_id).
# NULL twins are kept in place (no migration target).
# Source: query run 2026-04-19 against live mcp_server_registry.
BOOTSTRAP_MIGRATIONS = [
    ("a1b2c3d4e5f60001", "4ba4ace31b3d2034fa217eb7a30753c5"),  # server-filesystem
    ("a1b2c3d4e5f60002", "b99ef05bc0172ef81161ac75d72cb682"),  # server-github
    ("a1b2c3d4e5f60003", "b21274b9e233a0d504413912f99f0af5"),  # server-postgres
    ("a1b2c3d4e5f60004", "a9b2f372a955bdb0b86d64627ab9d23a"),  # server-slack
    ("a1b2c3d4e5f60005", "fdffa2e1dba17cc7e0d31a055d2a634f"),  # server-brave-search
    ("a1b2c3d4e5f60006", "1a2184246a7770bec55912e495288de4"),  # server-google-maps
    ("a1b2c3d4e5f60007", "3badeb0eedda54ffcce94672a6545f8b"),  # server-memory
    ("a1b2c3d4e5f60008", "f70c5480941c18e1f4c3d2c8a1399f51"),  # server-puppeteer
    ("a1b2c3d4e5f60011", "91f3202d5a83380f0f6725cd9b70126a"),  # mcp-server-kubernetes
    ("a1b2c3d4e5f60012", "b894107a38803c1db33a2f468d722328"),  # mcp-server-docker
    ("a1b2c3d4e5f60015", "604d5373ea3a94ee4bd648f46e4686dc"),  # server-everything
]

ORPHAN_BOOTSTRAPS = [
    "a1b2c3d4e5f60009",  # server-everart (no npm canonical yet)
    "a1b2c3d4e5f60010",  # server-gdrive
    "a1b2c3d4e5f60013",  # mcp-server-browserbase
    "a1b2c3d4e5f60014",  # claude-code-mcp
]

DRY_RUN = "--dry-run" in sys.argv
CODE_ONLY = "--code-only" in sys.argv
DATA_ONLY = "--data-only" in sys.argv


# ---- Helpers ----------------------------------------------------------

def _backup(path: Path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def ws_query(sql: str, params: list = None) -> list:
    r = requests.post(QUERY_URL, json={"sql": sql, "params": params or []}, timeout=30)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "rows" in body:
        return body["rows"]
    return body if isinstance(body, list) else []


def ws_execute(sql: str, params: list = None) -> dict:
    r = requests.post(EXECUTE_URL, json={"sql": sql, "params": params or [],
                                         "agent_id": "patch_threat_dedupe",
                                         "wait": True}, timeout=60)
    r.raise_for_status()
    return r.json()


# ---- Fix 1: patch threat_intel_ingestor to dedupe before insert -------

INGESTOR_OLD_MARKER = "def ws_write(table: str, rows: Any, wait: bool = True) -> Dict:"

# We inject a new helper `threat_already_recorded(server_id, osv_id)` and
# modify both write-sites (process_world_articles, fetch_osv_vulnerabilities)
# to call it before writing. Evidence strings start with 'OSV:GHSA-...' or
# 'OSV:MAL-...' which is the natural dedup key for OSV-sourced threats.
# For world_articles threats (not OSV), fall back to exact evidence match.

DEDUP_HELPER_CODE = '''
def _extract_osv_id(evidence: str) -> str:
    """Extract OSV identifier from evidence string if present.
    Evidence format: 'OSV:GHSA-xxxx-yyyy-zzzz | ...' or 'OSV:MAL-YYYY-NNNN | ...'
    Returns the OSV ID (e.g. 'GHSA-xxxx-yyyy-zzzz') or empty string."""
    if not evidence or not evidence.startswith("OSV:"):
        return ""
    # Grab everything between 'OSV:' and the first ' | ' separator
    head = evidence.split(" | ", 1)[0]
    return head[4:] if head.startswith("OSV:") else ""


def threat_already_recorded(server_id: str, evidence: str) -> bool:
    """Return True if this (server_id, threat-identity) already exists.
    For OSV threats, uniqueness key is (server_id, osv_id).
    For non-OSV threats (world_articles), uniqueness key is (server_id, exact evidence).
    Used to make ingestion idempotent across 2h cycles."""
    osv_id = _extract_osv_id(evidence)
    if osv_id:
        sql = ("SELECT 1 FROM mcp_threat_associations "
               "WHERE server_id = ? AND evidence LIKE ? LIMIT 1")
        like_pattern = f"OSV:{osv_id}%"
        try:
            rows = ws_query(sql, [server_id, like_pattern])
            return bool(rows)
        except Exception as e:
            log.warning(f"dedup check failed for {server_id}/{osv_id}: {e}")
            return False  # fail open -- better to duplicate than skip
    else:
        sql = ("SELECT 1 FROM mcp_threat_associations "
               "WHERE server_id = ? AND evidence = ? LIMIT 1")
        try:
            rows = ws_query(sql, [server_id, evidence])
            return bool(rows)
        except Exception as e:
            log.warning(f"dedup check failed for {server_id}: {e}")
            return False


'''


def patch_ingestor() -> bool:
    """Inject dedup helpers + modify write-sites. Returns True if changed."""
    if not THREAT_INGESTOR.exists():
        print(f"  [FAIL] target not found: {THREAT_INGESTOR}")
        return False

    src = THREAT_INGESTOR.read_text()

    if "threat_already_recorded" in src:
        print("  [skip] ingestor already patched")
        return True

    # A) Inject helpers BEFORE the ws_write definition (appears once in file)
    if INGESTOR_OLD_MARKER not in src:
        print(f"  [FAIL] anchor not found in ingestor: {INGESTOR_OLD_MARKER}")
        return False
    new_src = src.replace(
        INGESTOR_OLD_MARKER,
        DEDUP_HELPER_CODE + INGESTOR_OLD_MARKER,
        1,
    )

    # B) Modify process_world_articles write site
    waa_old = (
        "        severity = determine_severity(title + ' ' + summary)\n"
        "        evidence = f\"{title} | {url}\"\n"
        "        for server in matching_servers:\n"
        "            server_id = server.get('server_id')\n"
        "            try:\n"
        "                ws_write('mcp_threat_associations', {"
    )
    waa_new = (
        "        severity = determine_severity(title + ' ' + summary)\n"
        "        evidence = f\"{title} | {url}\"\n"
        "        for server in matching_servers:\n"
        "            server_id = server.get('server_id')\n"
        "            if threat_already_recorded(server_id, evidence):\n"
        "                continue  # dedupe: this article already recorded for this server\n"
        "            try:\n"
        "                ws_write('mcp_threat_associations', {"
    )
    if waa_old not in new_src:
        print("  [FAIL] world_articles write anchor not found")
        return False
    new_src = new_src.replace(waa_old, waa_new, 1)

    # C) Modify fetch_osv_vulnerabilities write site
    osv_old = (
        "                evidence = f\"OSV:{vuln_id} | {summary or details}\"\n"
        "                try:\n"
        "                    ws_write('mcp_threat_associations', {"
    )
    osv_new = (
        "                evidence = f\"OSV:{vuln_id} | {summary or details}\"\n"
        "                if threat_already_recorded(server_id, evidence):\n"
        "                    continue  # dedupe: OSV ID already recorded for this server\n"
        "                try:\n"
        "                    ws_write('mcp_threat_associations', {"
    )
    if osv_old not in new_src:
        print("  [FAIL] osv write anchor not found")
        return False
    new_src = new_src.replace(osv_old, osv_new, 1)

    # Validate
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return False

    if DRY_RUN:
        print("  [dry-run] ingestor would be patched (3 edits)")
        return True

    _backup(THREAT_INGESTOR)
    THREAT_INGESTOR.write_text(new_src)
    print("  [patch A] threat_already_recorded() helper injected")
    print("  [patch B] process_world_articles dedup guard added")
    print("  [patch C] fetch_osv_vulnerabilities dedup guard added")
    return True


# ---- Fix 2: cleanup duplicates in existing threats ---------------------

def dedupe_existing_threats() -> int:
    """Delete duplicate (server_id, evidence) rows, keeping oldest per group.
    Returns count deleted."""
    count_before = ws_query("SELECT COUNT(*) AS n FROM mcp_threat_associations")[0]["n"]
    distinct_before = ws_query(
        "SELECT COUNT(*) AS n FROM (SELECT DISTINCT server_id, evidence FROM mcp_threat_associations)"
    )[0]["n"]
    to_delete = count_before - distinct_before
    print(f"  [current] {count_before} rows; {distinct_before} distinct (server_id, evidence) pairs")
    print(f"  [target]  delete {to_delete} duplicate rows")

    if DRY_RUN:
        print("  [dry-run] no DELETE executed")
        return 0

    if to_delete == 0:
        print("  [skip] already deduped")
        return 0

    # DuckDB dedup pattern: use ROW_NUMBER to flag which to keep, delete rest
    # Pulling the 'id' column because mcp_threat_associations has a BIGINT PK
    victims = ws_query("""
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY server_id, evidence
                ORDER BY reported_at ASC, id ASC
            ) AS rn
            FROM mcp_threat_associations
        ) WHERE rn > 1
    """)
    print(f"  [collect] {len(victims)} duplicate row-ids identified")

    # Batch the delete into chunks to avoid overrunning write_service
    CHUNK = 500
    deleted = 0
    for i in range(0, len(victims), CHUNK):
        chunk = [v["id"] for v in victims[i:i+CHUNK]]
        placeholders = ",".join(["?"] * len(chunk))
        ws_execute(f"DELETE FROM mcp_threat_associations WHERE id IN ({placeholders})", chunk)
        deleted += len(chunk)
        if (i // CHUNK) % 5 == 4:
            print(f"    ... {deleted}/{len(victims)} deleted")
    print(f"  [done] {deleted} duplicate rows deleted")

    count_after = ws_query("SELECT COUNT(*) AS n FROM mcp_threat_associations")[0]["n"]
    print(f"  [verify] {count_before} -> {count_after} rows (expected {distinct_before})")
    return deleted


# ---- Fix 2b: migrate bootstrap short-id rows to canonical twins --------

def canonicalize_bootstrap_ids() -> int:
    """Migrate threats + history from 16-char bootstrap server_ids to their
    32-char canonical twins, then delete the bootstrap registry rows.
    Returns count of bootstrap entries removed."""
    removed = 0
    for bad_id, canonical in BOOTSTRAP_MIGRATIONS:
        # Count what we're about to migrate
        t_before = ws_query(
            "SELECT COUNT(*) AS n FROM mcp_threat_associations WHERE server_id = ?",
            [bad_id],
        )[0]["n"]
        h_before = ws_query(
            "SELECT COUNT(*) AS n FROM mcp_definition_history WHERE server_id = ?",
            [bad_id],
        )[0]["n"]
        registry_exists = ws_query(
            "SELECT 1 FROM mcp_server_registry WHERE server_id = ?",
            [bad_id],
        )

        if not registry_exists and t_before == 0 and h_before == 0:
            continue  # already canonicalized

        print(f"  [migrate] {bad_id} -> {canonical}  (threats={t_before}, history={h_before})")

        if DRY_RUN:
            continue

        # Update threats to point at canonical server_id
        # Then dedupe will handle any collisions on re-run
        if t_before > 0:
            ws_execute(
                "UPDATE mcp_threat_associations SET server_id = ? WHERE server_id = ?",
                [canonical, bad_id],
            )
        if h_before > 0:
            ws_execute(
                "UPDATE mcp_definition_history SET server_id = ? WHERE server_id = ?",
                [canonical, bad_id],
            )
        # Delete the bootstrap registry row
        if registry_exists:
            ws_execute(
                "DELETE FROM mcp_server_registry WHERE server_id = ?",
                [bad_id],
            )
            removed += 1

    if ORPHAN_BOOTSTRAPS and not DRY_RUN:
        print(f"  [keep] {len(ORPHAN_BOOTSTRAPS)} orphan bootstrap ids kept (no canonical twin)")
        print(f"         {', '.join(ORPHAN_BOOTSTRAPS)}")

    return removed


def final_dedup_pass():
    """After canonicalization migration, some new dupes may exist
    (bootstrap threat X migrated onto canonical already having threat X).
    Run dedup once more to clean those up."""
    print("  [post-migration] running dedup pass to clean collisions")
    dedupe_existing_threats()


# ---- Main -------------------------------------------------------------

def main():
    print("=" * 60)
    print("patch_threat_dedupe_and_canonicalize.py")
    if DRY_RUN:
        print("  MODE: DRY RUN (no writes)")
    elif CODE_ONLY:
        print("  MODE: code patch only (skip DB cleanup)")
    elif DATA_ONLY:
        print("  MODE: data cleanup only (skip ingestor patch)")
    print("=" * 60)

    # Fix 1: code patch
    if not DATA_ONLY:
        print("\n-- Fix 1: patch threat_intel_ingestor for dedup --")
        if not patch_ingestor():
            print("  [ABORT] code patch failed")
            return 2

    if not CODE_ONLY:
        print("\n-- Fix 2: dedupe existing mcp_threat_associations --")
        dedupe_existing_threats()

        print("\n-- Fix 2b: canonicalize bootstrap short-id rows --")
        canonicalize_bootstrap_ids()

        print("\n-- Final pass: dedupe post-migration collisions --")
        final_dedup_pass()

    print("\n" + "=" * 60)
    if DRY_RUN:
        print("DRY RUN complete. Re-run without --dry-run to apply.")
    else:
        print("Done. Verify with:")
        print("  SELECT COUNT(*) FROM mcp_threat_associations;")
        print("  SELECT COUNT(*) FROM mcp_server_registry WHERE LENGTH(server_id) != 32;")
        print("\nRestart threat_intel_ingestor to pick up dedup code:")
        print("  pkill -f 'daemon_wrapper.sh threat_intel_ingestor' ; sleep 2 ; \\")
        print("    rm -f /var/run/zo/threat_intel_ingestor.pid ; \\")
        print("    nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh threat_intel_ingestor \\")
        print("      /home/workspace/zo_sentinel/threat_intel_ingestor.py \\")
        print("      >> /home/workspace/logs/threat_intel_ingestor.log 2>&1 &")
    return 0


if __name__ == "__main__":
    sys.exit(main())