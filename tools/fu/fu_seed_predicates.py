#!/usr/bin/env python3
"""One-shot seeding of `- verify:` predicates for the open P0/P1 defects.

These are DRAFTS. Every one is seeded with `verify_seen_red: NEVER`, which
means fu_verify.py will run it and record the verdict but will NOT act on a
green. A predicate earns trust only by being observed RED against the live
system -- at which point the runner stamps it automatically. So a wrong
predicate here cannot silently close anything; the worst case is a probe that
is green from birth, which shows up in `--stats` as trusted=0 and is exactly
the signal that it tests nothing.

Read paths used (all read-only):
  * write-service query bus at 127.0.0.1:8772/query  (the documented read path)
  * service_health table                             (the reliable liveness probe, FU-115)
  * flyctl auth whoami                               (token liveness, FU-134/149)
  * git ls-remote / rev-parse                        (tree comparison)
  * gh api                                           (workflow + PR state)
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fu_ledger  # noqa: E402
import fu_lock  # noqa: E402

DEFAULT_LEDGER = r"D:\zo\Zocomputer Agents\FOLLOWUPS.md"
BACKUP_ROOT = r"D:\zo\Zocomputer Agents\_followup_backups"

# The write-service query bus binds loopback ON THE RUNTIME (start_all.sh sets
# PYTHONPATH=/home/workspace/zo_sentinel; `hostname` there is `modal`). It was
# never reachable from the tower and was never meant to be -- the first live
# sweep's 13 connection-refused results were a wrong-host error, not an outage.
# Tower-side predicates therefore go through zo_probe.py, which EXECUTES on the
# runtime via the Zo MCP bridge.
QUERY_ENDPOINT = os.environ.get("FU_QUERY_ENDPOINT", "http://127.0.0.1:8772/query")
ZO_PROBE = os.environ.get(
    "FU_ZO_PROBE", r"D:\zo\Zocomputer Agents\_tools\zo_probe.py")

# Absolute, like ZO_PROBE above: a predicate runs from whatever cwd the verifier
# happens to hold, so a repo-relative path here would be a silent red.
FLY_PROBE = os.environ.get(
    "FU_FLY_PROBE",
    str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "fly_token.py"))
FLY_PROBE_CMD = 'python "%s" --probe' % FLY_PROBE


def sql_assert(sql: str, py_test: str) -> str:
    """A predicate that queries the mesh bus and asserts on the result.

    Routed through `zo_probe.py`, which executes the query ON THE ZOCOMPUTER
    RUNTIME via the Zo MCP bridge. The bus (`write_service`, :8772) binds
    loopback on the runtime and is NOT reachable from the tower -- that is by
    design, not an outage. The bridge is execution, not routing.

    Preserves the three-state contract end to end: 0 GREEN / 1 RED /
    2 UNKNOWN, carried across the bridge by sentinel tokens because
    `zo_call.py` does not propagate remote exit codes.
    """
    return ('python "%s" --sql "%s" --assert "%s"'
            % (ZO_PROBE, sql.replace('"', "'"), py_test.replace('"', "'")))


PREDICATES = {
    # ---- infrastructure / credentials -----------------------------------
    # A BARE `flyctl auth whoami` READS THE CLOCK, NOT THE CREDENTIAL. It exits 1
    # on every box where the client-side 720h re-login timer has rolled over,
    # even while that same credential is authenticating a 494MB moat backup
    # (measured 2026-09-03, hours_remaining=-37.5). --probe hydrates from
    # AgentVault first, so it fails only when auth has ACTUALLY failed.
    "FU-134": (FLY_PROBE_CMD,
               "hydrated probe answers live; the 720h timer has no veto"),
    "FU-149": (FLY_PROBE_CMD,
               "same token, tower side; every prod-PG tunnel depends on this "
               "probe exiting 0"),

    # ---- daemon liveness (FU-115's own 'reliable probe') ----------------
    "FU-115": (sql_assert(
        "SELECT COUNT(*) FROM service_health WHERE last_heartbeat < now() - interval '15 minutes'",
        "v is not None and int(v)==0"),
        "no service_health row is stale; the log-file probe is not consulted at all"),
    "FU-036": (sql_assert(
        "SELECT EXTRACT(EPOCH FROM (now()-last_heartbeat)) FROM service_health "
        "WHERE service='goose_runner'",
        "v is not None and float(v)<900"),
        "goose_runner heartbeat under 15 min -- the 61.6h blindness cannot recur silently"),

    # ---- moat backup ----------------------------------------------------
    "FU-024": (sql_assert(
        "SELECT EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600 FROM mesh_events "
        "WHERE event_type LIKE '%backup%'",
        "v is not None and float(v)<36"),
        "a backup event landed within 36h; silence is not treated as success"),
    "FU-107": (sql_assert(
        "SELECT EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600 FROM mesh_events "
        "WHERE event_type LIKE '%backup%' AND event_type NOT LIKE '%fail%'",
        "v is not None and float(v)<36"),
        "a SUCCEEDING backup inside the nightly window, i.e. COPY throughput is feasible"),

    # ---- corpus quality / coverage --------------------------------------
    # MERGE_AUDIT_2026-08-23 B1. These seven predicates were seeded against
    # `server_scores`, `servers` and `score_runs`. Those three names exist on
    # NO plane: not among the 44 tables on the bus, not as a __tablename__ in
    # app/models.py, and in no migration or schema snapshot. They were never
    # right -- sibling predicates in this same dict (FU-115, FU-036, FU-107)
    # query service_health and mesh_events, which do exist.
    #
    # So there is no "re-target at the app database" option: there is nothing
    # to re-target TO. They are mapped to the real tables, which carry the same
    # names on both planes (app/models.py declares __tablename__ =
    # "mcp_server_registry" / "mcp_llm_axis_scores"), so the mesh bus -- this
    # harness's only read path -- is also the documented one:
    #
    #   server_scores -> mcp_server_registry   (it is the table that actually
    #                    holds risk_tier AND confidence, which is what FU-093
    #                    and FU-058 assert on)
    #   server_scores -> mcp_llm_axis_scores   (where a per-server SCORING event
    #                    lives, with scored_at -- FU-090, FU-108)
    #   servers       -> mcp_server_registry
    #   score_runs    -> agent_runs            (the only populated run ledger:
    #                    8363 rows and a status column; bulk_assess_jobs is empty)
    #
    # Only the table/column names changed. Each threshold and comparison is left
    # exactly as seeded, so a predicate's MEANING is not quietly redesigned here.
    # Verified live: all seven now return a value and resolve GREEN or RED --
    # none is UNKNOWN.
    "FU-093": (sql_assert(
        "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IS NOT NULL "
        "AND confidence IS NOT NULL)/NULLIF(COUNT(*),0),2) FROM mcp_server_registry",
        "v is not None and float(v)>50"),
        "over half the scored moat carries a defensible signal, not adapter garbage"),
    "FU-058": (sql_assert(
        "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IN ('HIGH','CRITICAL'))"
        "/NULLIF(COUNT(*),0),2) FROM mcp_server_registry",
        "v is not None and float(v)<90"),
        "HIGH+CRITICAL below 90% -- the tier carries information again"),
    "FU-090": (sql_assert(
        "SELECT ROUND(100.0*(SELECT COUNT(DISTINCT server_id) FROM mcp_llm_axis_scores)"
        "/NULLIF((SELECT COUNT(*) FROM mcp_server_registry),0),2)",
        "v is not None and float(v)>60"),
        "assessment coverage back above 60% of the moat"),
    # first_seen is the registry's discovery timestamp; there is no created_at.
    "FU-054": (sql_assert(
        "SELECT COUNT(*) FROM mcp_server_registry "
        "WHERE first_seen > now() - interval '24 hours'",
        "v is not None and int(v)>=100"),
        "discovery intake above 100 rows/day, not the ~2/day collapse"),
    "FU-108": (sql_assert(
        "SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE scored_at > '2026-07-26'",
        "v is not None and int(v)>0"),
        "the validated re-score actually landed in the table rather than stranding"),

    # ---- pipeline / run integrity ---------------------------------------
    "FU-104": (sql_assert(
        "SELECT COUNT(*) FROM agent_runs WHERE status IS NULL OR status=''",
        "v is not None and int(v)==0"),
        "no score run is left without a terminal status -- a silent exit is recorded"),
    # started_at is agent_runs' creation timestamp; there is no created_at.
    "FU-001": (sql_assert(
        "SELECT COUNT(*) FROM agent_runs "
        "WHERE started_at > now() - interval '14 days'",
        "v is not None and int(v)>0"),
        "the wave/rescore harnesses are writing the run ledger at all"),

    # ---- deploy / runtime drift -----------------------------------------
    "FU-065": ("python -c \"import subprocess,sys;"
               "a=subprocess.run(['git','ls-remote','origin','refs/heads/main'],"
               "cwd=r'D:\\\\zo\\\\zo-sentinel\\\\zo-sentinel',capture_output=True,text=True)"
               ".stdout.split()[:1];"
               "b=subprocess.run(['git','rev-parse','HEAD'],"
               "cwd=r'D:\\\\zo\\\\zo-sentinel\\\\zo-sentinel',capture_output=True,text=True)"
               ".stdout.strip();"
               "sys.exit(0 if a and a[0]==b else 1)\"",
               "the local checkout is exactly origin/main, not chronically behind"),
    "FU-028": ("curl -sf --max-time 20 https://mcplookup.app/version",
               "the runtime answers /version at all, so drift is observable"),
    "FU-027": ("python -c \"import time,urllib.request,sys;"
               "t=time.time();urllib.request.urlopen('https://mcplookup.app/freshness',"
               "timeout=30).read();sys.exit(0 if time.time()-t<10 else 1)\"",
               "cold /freshness under 10s, not ~55s"),
    "FU-124": ("python -c \"import urllib.request,sys;"
               "ok=all(urllib.request.urlopen('https://mcplookup.app/'+p,timeout=20).status==200 "
               "for p in ['robots.txt','sitemap.xml']);sys.exit(0 if ok else 1)\"",
               "robots.txt and sitemap.xml are served -- the crawler gap is closed"),

    # ---- builder / goose correctness (repo-level invariants) -------------
    "FU-109": (sql_assert(
        "SELECT COUNT(*) FROM mesh_events WHERE event_type='promoter_hold' "
        "AND created_at > now() - interval '24 hours'",
        "v is not None and int(v)==0"),
        "no promoter HOLD in the last 24h -- the liveness gate is passing"),
    "FU-120": ("python -c \"import subprocess,sys;"
               "r=subprocess.run(['git','grep','-l','\\\\[service\\\\]','--','services/'],"
               "cwd=r'D:\\\\zo\\\\zo-sentinel\\\\zo-sentinel',capture_output=True,text=True);"
               "n=len([x for x in r.stdout.split() if x]);"
               "t=subprocess.run(['git','ls-files','services/*/service.toml'],"
               "cwd=r'D:\\\\zo\\\\zo-sentinel\\\\zo-sentinel',capture_output=True,text=True);"
               "m=len([x for x in t.stdout.split() if x]);"
               "sys.exit(0 if m>0 and n==m else 1)\"",
               "every service.toml carries the [service] header -- one shape, not two"),
    "FU-116": ("gh api repos/rob531/zo-sentinel/contents/.github/workflows "
               "--jq \".[].name\" | findstr /I canary",
               "the canary guards exist as a workflow, i.e. wired as a gate not just a file"),
}

# Deliberately NOT given a predicate. Each of these needs a probe that does not
# exist yet and that I cannot ground in anything the entry documents. Writing a
# `sys.exit(1)` placeholder here would turn the linter green while changing
# nothing -- the exact move that "fixed" FU-114 by appending an empty key. They
# are left as `verify: NONE`, which trips E7 and keeps them loudly visible as
# the remaining articulation debt.
UNARTICULATED = {
    "FU-031": "needs a real Tier-0 acceptance-self-test skip-rate probe against the builder",
    "FU-117": "needs a probe that starts goose with a deliberately dead stdio extension "
              "and asserts non-zero exit",
    "FU-119": "needs the canary to drive the transport the mesh actually uses; no probe "
              "exists for the real lane yet",
    "FU-101": "shepherd ROLE, not a defect -- reclassify as class:directive rather than "
              "inventing an acceptance test",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing verify (used to correct a bad predicate)")
    args = ap.parse_args()

    with open(args.ledger, encoding="utf-8") as fh:
        original_text = fh.read()
    lines = original_text.split("\n")

    targets = [f.id for f in fu_ledger.parse(lines)
               if f.is_open() and f.priority in ("P0", "P1")
               and (f.fu_class or "defect") == "defect"]

    seeded, skipped, missing = [], [], []
    for fu_id in targets:
        if fu_id not in PREDICATES and fu_id not in UNARTICULATED:
            missing.append(fu_id)

    for fu_id, reason in UNARTICULATED.items():
        entries = {f.id: f for f in fu_ledger.parse(lines)}
        fu = entries.get(fu_id)
        if fu is None or fu.verify_cmd or fu.verify_is_none:
            continue
        fu_ledger.insert_key(lines, fu, "verify",
                             "%s - %s" % (fu_ledger.NO_VERIFY, reason), before="log")

    for fu_id, (cmd, why) in PREDICATES.items():
        entries = {f.id: f for f in fu_ledger.parse(lines)}
        fu = entries.get(fu_id)
        if fu is None:
            continue
        if fu.verify_cmd and not args.force:
            skipped.append(fu_id)
            continue
        fu_ledger.insert_key(lines, fu, "verify", "`%s`  # %s" % (cmd, why), before="log")
        entries = {f.id: f for f in fu_ledger.parse(lines)}
        fu_ledger.insert_key(lines, entries[fu_id], "verify_seen_red",
                             fu_ledger.NEVER_RED, before="log")
        seeded.append(fu_id)

    print("open P0/P1 defects : %d" % len(targets))
    print("seeded             : %d  %s" % (len(seeded), ", ".join(sorted(seeded))))
    if skipped:
        print("already had verify : %s" % ", ".join(sorted(skipped)))
    print("left unarticulated : %d  %s  (E7 will keep flagging these)"
          % (len(UNARTICULATED), ", ".join(sorted(UNARTICULATED))))
    if missing:
        print("UNACCOUNTED FOR    : %s" % ", ".join(sorted(missing)))

    if args.apply:
        stamp = datetime.now(timezone.utc)
        bdir = os.path.join(BACKUP_ROOT, stamp.strftime("%Y-%m-%d"), "seed-predicates")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(args.ledger, os.path.join(
            bdir, "FOLLOWUPS.md.%s.bak" % stamp.strftime("%H%M%SZ")))
        try:
            with fu_lock.ledger_txn(args.ledger) as txn:
                if fu_lock.digest("\n".join(txn.lines)) != fu_lock.digest(original_text):
                    raise fu_lock.LedgerChanged(
                        "ledger moved between planning and apply; re-run to replan")
                txn.lines[:] = lines
        except (fu_lock.LedgerChanged, fu_lock.LedgerBusy) as exc:
            print("ABORTED: %s" % exc, file=sys.stderr)
            return 3
        print("APPLIED (backup in %s)" % bdir)
    else:
        print("DRY RUN -- pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
