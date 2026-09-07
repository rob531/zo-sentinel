"""FU-242 — Rewritten predicates for the nine FUs that queried non-existent tables.

Context
-------
The original predicates referenced tables `score_runs`, `servers`, and `server_scores`
that do not exist in the :8772 DuckDB catalog. All nine returned rc=2 (UNKNOWN).

This script documents the correct table mapping (established 2026-09-07) and provides
the rewritten SQL strings that should replace the `verify:` lines in each FU.

Table mapping (confirmed against live :8772 catalog on 2026-09-07):
  score_runs    ->  agent_runs       (has started_at, status, id)
  servers       ->  mcp_server_registry  (canonical server table, has first_seen)
  server_scores ->  mcp_server_registry  (has risk_tier, confidence, trust_score)
                    mcp_signal_scores    (has server_id, scored_at, for FU-108)
  mesh_events   ->  mesh_events          (EXISTS -- but no 'backup' event_type exists;
                                          COALESCE needed to avoid NULL->UNKNOWN)

How to use
----------
Run this script on the tower to print the rewritten verify: lines for all nine FUs.
The output can be copy-pasted into FOLLOWUPS.md via fu_append_log.py by the chairman.

Negative control (required by H3)
----------------------------------
Each rewritten predicate must have been observed RED before being declared fixed.
  FU-054: 2 servers in 24h < 100 threshold -> rc=1 (RED, genuine)
  FU-093: 0.03% rows have both risk_tier+confidence -> rc=1 (RED, genuine)
  FU-024: no backup events ever -> COALESCE gives 9999h > 36h -> rc=1 (RED, genuine)
  FU-107: same as FU-024 -> rc=1 (RED, genuine)

These are genuine failures — not artificial. A GREEN that arrives by weakening the
assertion would be the FU-114 sin; these rewrites preserve the original intent.
"""
from __future__ import annotations

ZO_PROBE = r"D:\zo\Zocomputer Agents\_tools\zo_probe.py"

# ---------------------------------------------------------------------------
# Rewritten predicate table
# ---------------------------------------------------------------------------
# Each entry: (fu_id, new_sql, assertion, rationale)
REWRITES: list[tuple[str, str, str, str]] = [
    (
        "FU-001",
        "SELECT COUNT(*) FROM agent_runs WHERE started_at > now() - INTERVAL '14 days'",
        "v is not None and int(v)>0",
        "score_runs->agent_runs; started_at replaces created_at; wave/rescore harnesses write agent_runs rows",
    ),
    (
        "FU-024",
        "SELECT COALESCE(EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600, 9999) FROM mesh_events WHERE event_type LIKE '%backup%'",
        "v is not None and float(v)<36",
        "mesh_events EXISTS but has no backup events; COALESCE(NULL,9999) -> 9999h > 36h -> RED (genuine); NULL->UNKNOWN masked the failure before",
    ),
    (
        "FU-054",
        "SELECT COUNT(*) FROM mcp_server_registry WHERE first_seen > now() - INTERVAL '24 hours'",
        "v is not None and int(v)>=100",
        "servers->mcp_server_registry; first_seen replaces created_at; threshold unchanged",
    ),
    (
        "FU-058",
        "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IN ('HIGH','CRITICAL'))/NULLIF(COUNT(*),0),2) FROM mcp_server_registry",
        "v is not None and float(v)<90",
        "server_scores->mcp_server_registry which has risk_tier column; assertion unchanged",
    ),
    (
        "FU-090",
        "SELECT ROUND(100.0*(SELECT COUNT(DISTINCT server_id) FROM mcp_signal_scores)/NULLIF((SELECT COUNT(*) FROM mcp_server_registry),0),2)",
        "v is not None and float(v)>60",
        "server_scores coverage: scored set from mcp_signal_scores, total from mcp_server_registry; both tables exist",
    ),
    (
        "FU-093",
        "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IS NOT NULL AND confidence IS NOT NULL)/NULLIF(COUNT(*),0),2) FROM mcp_server_registry",
        "v is not None and float(v)>50",
        "server_scores->mcp_server_registry; risk_tier and confidence columns present; assertion unchanged",
    ),
    (
        "FU-104",
        "SELECT COUNT(*) FROM agent_runs WHERE status IS NULL OR status=''",
        "v is not None and int(v)==0",
        "score_runs->agent_runs; status column exists in agent_runs; assertion unchanged",
    ),
    (
        "FU-107",
        "SELECT COALESCE(EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600, 9999) FROM mesh_events WHERE event_type LIKE '%backup%' AND event_type NOT LIKE '%fail%'",
        "v is not None and float(v)<36",
        "mesh_events EXISTS but has no backup events (confirmed 2026-09-07); COALESCE(NULL,9999) -> RED (genuine); same rationale as FU-024",
    ),
    (
        "FU-108",
        "SELECT COUNT(*) FROM mcp_signal_scores WHERE scored_at > '2026-07-26'",
        "v is not None and int(v)>0",
        "server_scores->mcp_signal_scores for scored_at; 8.4M rows confirmed post-2026-07-26; assertion unchanged",
    ),
]


def print_rewritten_verify_lines() -> None:
    print("# FU-242 rewritten verify: lines")
    print("# Copy each line as the new verify: value for its FU in FOLLOWUPS.md")
    print()
    for fu, sql, assertion, rationale in REWRITES:
        verify_cmd = (
            f'python "{ZO_PROBE}" '
            f'--sql "{sql}" '
            f'--assert "{assertion}"'
        )
        print(f"# {fu} — {rationale}")
        print(f'- verify: `{verify_cmd}`')
        print()


def print_zo_probe_commands() -> None:
    """Print the exact zo_probe.py commands to validate each rewrite."""
    import sys
    print("# Commands to validate each rewritten predicate (run from tower):")
    print()
    for fu, sql, assertion, rationale in REWRITES:
        print(f"# {fu}")
        print(
            f'python "{ZO_PROBE}" '
            f'--sql "{sql}" '
            f'--assert "{assertion}"'
        )
        print(f"echo exit code: $?")
        print()


if __name__ == "__main__":
    import sys
    if "--commands" in sys.argv:
        print_zo_probe_commands()
    else:
        print_rewritten_verify_lines()
