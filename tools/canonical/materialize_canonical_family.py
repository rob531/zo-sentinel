#!/usr/bin/env python3
"""materialize_canonical_family: idempotent backfill of registry family identity.

Doctrine (ported from Commit-B canonicalizer, Feb-Apr 2026):
  STICKY   -- only rows with canonical_family IS NULL are written. Once set,
              a value changes only through a governance pass, never silently.
  DRIFT    -- --rederive recomputes for ALL rows and REPORTS disagreements
              (stdout JSON) without writing. Mirrors canonical_drift_log
              "detect, don't auto-update".
  IDEMPOTENT - a second --apply run writes 0 rows (verifiable exit summary).

Usage (tower-side, fly proxy auto-started like the rescore tooling):
  python materialize_canonical_family.py --dsn-file <path> --apply
  python materialize_canonical_family.py --dsn-file <path> --rederive
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from family_rules import derive_family

# FU-151: one shared credential path for every flyctl caller in this repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fly_token import ensure_proxy as _ensure_fly_proxy  # noqa: E402


def pg_conn(dsn_file, port=15432):
    import psycopg2
    # FU-151/FU-133: see delta_report.pg_conn -- same defect, same fix, and the
    # reason this module is patched in the SAME breath is that shipping the helper
    # to one caller is exactly what let the 2026-07-28 outage recur the next day.
    _ensure_fly_proxy(port, "mcplookup-db",
                      os.path.join(os.environ.get("TEMP", "/tmp"),
                                   "_flyctl_proxy_canonical_family.err"),
                      log=lambda m: print(m, file=sys.stderr, flush=True))
    dsn = open(dsn_file).read().strip()
    dsn = re.sub(r"@[^/:@]+(:\d+)?/", "@127.0.0.1:%d/" % port, dsn)
    dsn = re.sub(r"host=\S+", "host=127.0.0.1", dsn)
    dsn = re.sub(r"port=\d+", "port=%d" % port, dsn)
    return psycopg2.connect(dsn)


def main():
    from psycopg2.extras import execute_values
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn-file", required=True)
    ap.add_argument("--apply", action="store_true", help="sticky fill of NULL rows")
    ap.add_argument("--rederive", action="store_true", help="drift report, no writes")
    ap.add_argument("--batch", type=int, default=5000)
    args = ap.parse_args()
    if not (args.apply or args.rederive):
        ap.error("pick --apply or --rederive")

    con = pg_conn(args.dsn_file); cur = con.cursor()
    cur.execute("SET statement_timeout='600s'")
    where = "" if args.rederive else "where canonical_family is null"
    cur.execute("select server_id, url, metadata, canonical_family "
                "from mcp_server_registry " + where)
    rows = cur.fetchall()
    updates, drift, rule_counts = [], [], {}
    for sid, url, meta, current in rows:
        fam, rule = derive_family(sid, url, meta)
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        if args.rederive:
            if current is not None and current != fam:
                drift.append({"server_id": sid, "current": current,
                              "proposed": fam, "proposed_rule": rule})
            continue
        updates.append((sid, fam, rule))

    written = 0
    if args.apply and updates:
        for i in range(0, len(updates), args.batch):
            chunk = updates[i:i + args.batch]
            execute_values(cur,
                "update mcp_server_registry r set canonical_family = v.fam, "
                "canonical_rule = v.rule, canonical_set_at = now() "
                "from (values %s) as v(sid, fam, rule) "
                "where r.server_id = v.sid and r.canonical_family is null",
                chunk)
            written += len(chunk)
            con.commit()
    if args.apply:
        cur.execute("select count(*), count(canonical_family), "
                    "count(distinct canonical_family) from mcp_server_registry")
        total, filled, families = cur.fetchone()
        print(json.dumps({"mode": "apply", "candidates": len(updates),
                          "written": written, "rule_counts": rule_counts,
                          "registry_rows": total, "filled": filled,
                          "families": families,
                          "idempotent_proof": "rerun must report candidates=0"}))
    else:
        print(json.dumps({"mode": "rederive", "rows": len(rows),
                          "rule_counts": rule_counts, "drift": len(drift),
                          "drift_sample": drift[:25]}))
    con.close()


if __name__ == "__main__":
    sys.exit(main())
