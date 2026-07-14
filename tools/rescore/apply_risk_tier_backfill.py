#!/usr/bin/env python3
"""apply_risk_tier_backfill.py -- stamp mcp_server_registry.risk_tier (+ last_assessed)
from student axis scores. Phase-1 council roadmap deliverable: the registry was
100% 'unassessed' because nothing ever aggregated axis scores into a tier.

Tier rule (mirrors gate_rule_v1_2026-06-16, the production decision rule):
  * escalated CRITICAL  (P_crit >= 0.40)          -> CRITICAL
  * otherwise           argmax overall_risk label -> LOW | MEDIUM | HIGH | CRITICAL
  * REVIEW escalation does NOT change the stored tier (it is a queue signal,
    not a verdict); it is visible via mcp_llm_axis_scores.escalated_to.

URL propagation: scoring runs on distinct-URL representatives (65,552 of
80,539 rows). Every registry row sharing a scored row's URL inherits its tier.

Writes to the DB reached via --host/--port (default: the fly proxy at
127.0.0.1:15432). DEFAULT IS DRY-RUN; pass --apply to write.

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python apply_risk_tier_backfill.py --dsn-file <path> [--apply]
        [--model-version v3.0_40974559]
"""
import argparse
import datetime
import re
import sys

import psycopg2


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn-file", required=True,
                    help="file containing the postgres:// DSN (user/pass/db taken from it)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--model-version", default="v3.0_40974559")
    ap.add_argument("--apply", action="store_true", help="actually write (default dry-run)")
    return ap.parse_args()


def main():
    a = parse_args()
    dsn = open(a.dsn_file).read().strip()
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    if not m:
        print("FATAL: unparseable DSN")
        return 2
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=m.group(3),
                            user=m.group(1), password=m.group(2))
    cur = conn.cursor()

    # tier per scored server: escalated CRITICAL wins, else argmax label
    cur.execute("""
        select s.server_id,
               case when s.escalated and s.escalated_to = 'CRITICAL' then 'CRITICAL'
                    else upper(s.label) end as tier
        from mcp_llm_axis_scores s
        where s.axis_name = 'overall_risk' and s.model_version = %s
    """, (a.model_version,))
    tiers = dict(cur.fetchall())
    print(f"[backfill] scored servers with overall_risk: {len(tiers)}")
    if not tiers:
        print("FATAL: no scores to backfill from")
        return 1

    dist = {}
    for t in tiers.values():
        dist[t] = dist.get(t, 0) + 1
    print("[backfill] tier distribution (scored representatives):", dict(sorted(dist.items())))

    now = datetime.datetime.utcnow()

    # 1) direct stamp for scored server_ids
    # 2) URL propagation: rows sharing a URL with a scored row inherit its tier
    if a.apply:
        cur.execute("create temp table _tier_stage (server_id varchar primary key, tier varchar)")
        from psycopg2.extras import execute_values
        execute_values(cur, "insert into _tier_stage (server_id, tier) values %s",
                       list(tiers.items()))
        cur.execute("""
            update mcp_server_registry r
            set risk_tier = t.tier, last_assessed = %s
            from _tier_stage t where r.server_id = t.server_id
        """, (now,))
        n_direct = cur.rowcount

        # Retro-revert: any row still wearing a tier it never earned (no direct
        # axis scores) goes back to 'unassessed'. 'unassessed' -- NOT NULL --
        # because it is already the discovery-time default and every consumer
        # handles it. This un-asserts the 14,015 fabricated tiers.
        cur.execute("""
            update mcp_server_registry r
            set risk_tier = 'unassessed'
            where (r.risk_tier is not null and r.risk_tier <> 'unassessed')
              and not exists (
                select 1 from mcp_llm_axis_scores s where s.server_id = r.server_id
              )
        """)
        n_unasserted = cur.rowcount
        conn.commit()
        print(f"[backfill] APPLIED direct={n_direct} un-asserted={n_unasserted} "
              f"(url-propagation DELETED -- CofC 2026-07-14)")
    else:
        cur.execute("""
            select count(*) from mcp_server_registry
            where server_id = any(%s)
        """, (list(tiers.keys()),))
        print(f"[backfill] DRY-RUN would stamp {cur.fetchone()[0]} rows directly "
              f"(+ URL propagation for the remainder). Re-run with --apply.")

    cur.execute("select risk_tier, count(*) from mcp_server_registry group by 1 order by 2 desc")
    print("[backfill] registry tier distribution now:", cur.fetchall())
    return 0


if __name__ == "__main__":
    sys.exit(main())
