#!/usr/bin/env python3
"""
patch_enrichment_pipeline_schema_fixes.py

Three related schema-mismatch bugs in the enrichment pipeline, all
stemming from insufficient verification of actual table shapes before
writing code:

  BUG 1: signal_bridge.py uses 'created_at' column name in its fetch
         query. The actual mcp_signal_enrichments table has
         'computed_at'. Result: every fetch returns HTTP 400,
         signal_bridge has total_bridged=0 since deployment.

  BUG 2: ecosystems_enrichment_adapter.py writes to mcp_signal_enrichments
         via /write endpoint. That endpoint auto-injects an 'id' column.
         Adapter supplied its own id, but the table ALSO requires:
           - run_id (NOT NULL)
           - correct handling of the composite unique constraint on
             (server_id, enrichment_name, run_id)
         Our rows were missing run_id entirely -> 100% write failures.

  BUG 3: adapter's deterministic-hash id collides with existing auto-
         incremented ids on the table. Need to let WriteService
         auto-assign id, which it does via the /write path -- but we
         need to either: provide run_id OR skip the auto-id path by
         going through /execute.

Fixes:
  A. signal_bridge.py: s/created_at/computed_at/ in the ranked CTE and
     output SELECT. No schema changes.
  B. ecosystems_enrichment_adapter.py: rewrite _write_enrichment to
     include run_id (stable per-cycle value), let id auto-generate via
     WriteService (don't pass our own), and use /write with mode='insert'
     rather than upsert. Each adapter cycle produces new rows with a
     unique run_id; signal_bridge reads the latest via ORDER BY
     computed_at DESC.

Note: once signal_bridge starts working, it will cascade enrichments
INTO mcp_signal_scores. Gate 9 on next run should show community_signal
and temporal_stability diversity climb.

Idempotent via marker check on both files.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BRIDGE = Path("/home/workspace/zo_sentinel/signal_bridge.py")
ADAPTER = Path("/home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py")

# ---- Fix A: signal_bridge column name ---------------------------------

BRIDGE_OLD = '''def fetch_latest_enrichments() -> list[dict]:
    """Latest enrichment score per (server_id, enrichment_name)."""
    return ws_query(
        """
        WITH ranked AS (
            SELECT server_id, enrichment_name, score, evidence, created_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY server_id, enrichment_name
                       ORDER BY created_at DESC
                   ) AS rn
            FROM mcp_signal_enrichments
        )
        SELECT server_id, enrichment_name, score, evidence, created_at
        FROM ranked WHERE rn = 1
        """,
    )'''

BRIDGE_NEW = '''def fetch_latest_enrichments() -> list[dict]:
    """Latest enrichment score per (server_id, enrichment_name).
    Uses computed_at (actual table column) -- not created_at (an earlier
    typo that caused 100% fetch failures until this patch)."""
    return ws_query(
        """
        WITH ranked AS (
            SELECT server_id, enrichment_name, score, evidence, computed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY server_id, enrichment_name
                       ORDER BY computed_at DESC
                   ) AS rn
            FROM mcp_signal_enrichments
        )
        SELECT server_id, enrichment_name, score, evidence, computed_at
        FROM ranked WHERE rn = 1
        """,
    )'''

# ---- Fix B: adapter write path ---------------------------------------

ADAPTER_OLD = '''def _make_enrichment_id(server_id: str, enrichment_name: str) -> int:
    """Stable PK generator so upserts deduplicate cleanly.
    Matches the md5-first-8-hex pattern used elsewhere in ZO-SENTINEL."""
    id_str = f"{server_id}:{enrichment_name}"
    return int(hashlib.md5(id_str.encode()).hexdigest()[:8], 16) % (2**31)


def _write_enrichment(server_id: str, enrichment_name: str,
                      score: float, evidence: dict) -> bool:
    row = {
        "id": _make_enrichment_id(server_id, enrichment_name),
        "server_id": server_id,
        "enrichment_name": enrichment_name,
        "score": score,
        "evidence": json.dumps(evidence)[:2000],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    return ws_write(row)'''

ADAPTER_NEW = '''# Generated once per adapter invocation so each --once run produces a
# coherent batch of enrichments sharing a run_id. signal_bridge reads
# most-recent per (server_id, enrichment_name) via ORDER BY computed_at
# DESC, so multiple runs accumulate cleanly without upsert semantics.
_RUN_ID = f"ecosystems_adapter_{datetime.now(timezone.utc).strftime(\'%Y%m%d_%H%M%S\')}_{os.getpid()}"


def _write_enrichment(server_id: str, enrichment_name: str,
                      score: float, evidence: dict) -> bool:
    """Insert new enrichment row. Does NOT upsert -- each run creates a
    fresh row with this adapter instance\'s _RUN_ID. signal_bridge picks
    the most recent via computed_at DESC.

    Schema reminder: mcp_signal_enrichments requires NOT NULL on id,
    run_id, enrichment_name, server_id, score. WriteService auto-assigns
    id; we supply the others."""
    row = {
        "run_id": _RUN_ID,
        "server_id": server_id,
        "enrichment_name": enrichment_name,
        "score": score,
        "evidence": json.dumps(evidence)[:2000],
        "input_fingerprint": hashlib.md5(
            f"{server_id}:{enrichment_name}:{score}".encode()
        ).hexdigest()[:16],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    return ws_write(row)'''


def _backup(path: Path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def patch_file(path: Path, old: str, new: str, marker: str, label: str) -> bool:
    if not path.exists():
        print(f"  [FAIL] {label}: {path} not found")
        return False
    src = path.read_text()
    if marker in src:
        print(f"  [skip] {label}: already patched")
        return True
    if old not in src:
        print(f"  [FAIL] {label}: anchor not found verbatim")
        return False
    src = src.replace(old, new, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] {label}: AST invalid after patch: {e}")
        return False
    _backup(path)
    path.write_text(src)
    print(f"  [patch] {label}: applied")
    return True


def main():
    print("=" * 60)
    print("patch_enrichment_pipeline_schema_fixes.py")
    print("=" * 60)

    ok_a = patch_file(
        BRIDGE, BRIDGE_OLD, BRIDGE_NEW,
        marker="computed_at DESC",
        label="A: signal_bridge computed_at",
    )
    ok_b = patch_file(
        ADAPTER, ADAPTER_OLD, ADAPTER_NEW,
        marker="_RUN_ID = f",
        label="B: adapter schema-correct write",
    )

    if not (ok_a and ok_b):
        print("\n[ABORT] one or more patches failed")
        return 2

    print("\n" + "=" * 60)
    print("Done. Restart signal_bridge + re-run adapter:")
    print()
    print("  pkill -f 'daemon_wrapper.sh signal_bridge'")
    print("  sleep 2")
    print("  source /home/workspace/zo_mesh/.zo_env")
    print("  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh signal_bridge \\")
    print("    /home/workspace/zo_sentinel/signal_bridge.py \\")
    print("    >> /home/workspace/logs/signal_bridge.log 2>&1 &")
    print()
    print("  python3 /home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py --once")
    print()
    print("Verify adapter writes succeeded:")
    print("  curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\")
    print("    -d '{\"sql\":\"SELECT enrichment_name, COUNT(*) FROM mcp_signal_enrichments")
    print("          WHERE run_id LIKE \\'ecosystems_adapter_%\\' GROUP BY 1\"}'")
    print()
    print("Verify signal_bridge now bridges (wait ~5min for next cycle):")
    print("  tail /home/workspace/logs/signal_bridge.log")
    print("  # expect 'cycle complete: N enrichments seen, M bridged'")
    return 0


if __name__ == "__main__":
    sys.exit(main())