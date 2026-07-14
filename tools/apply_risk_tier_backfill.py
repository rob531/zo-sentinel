#!/usr/bin/env python3
"""tools/apply_risk_tier_backfill.py -- stamp mcp_server_registry.risk_tier
(+ last_assessed) from student axis scores, THEN route the result through
trust_gating_override (the anti-defamation cap).

REGRESSION LESSON (2026-07-04): the first Phase-1 backfill stamped raw student
tiers and left 8 official big-tech servers (azure-mcp, cloudflare/mcp,
googleapis/gcloud-mcp, ...) published as CRITICAL -- violating the standing
rule that verified-publisher MCPs are never published above MEDIUM (PR #672
semantics). The cap is now part of the backfill itself so no future rescore
can regress it: every HIGH/CRITICAL row is passed through trust_gate() and
capped rows record {raw_tier, trust_basis, capped_at} in metadata.trust_cap
(provenance of the override, THE LINE).

Tier rule (mirrors gate_rule_v1_2026-06-16):
  * escalated CRITICAL  (P_crit >= 0.40)          -> CRITICAL
  * otherwise           argmax overall_risk label -> LOW | MEDIUM | HIGH | CRITICAL
  * then trust_gate cap: trusted publisher + HIGH/CRITICAL -> MEDIUM

URL propagation: scoring runs on distinct-URL representatives; rows sharing a
scored row's URL inherit its tier. DEFAULT IS DRY-RUN; pass --apply to write.

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python tools/apply_risk_tier_backfill.py --dsn-file <path> [--apply]
        [--model-version v3.0_40974559] [--skip-trust-cap]
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trust_gating_override import trust_gate  # noqa: E402


def cap_for(url, name, tier):
    """(published_tier, trust_basis) if the trust cap changes `tier`, else None.
    Pure -- unit-tested without a DB."""
    if tier not in ("HIGH", "CRITICAL"):
        return None
    r = trust_gate(url, name, {"overall_risk": tier, "maintainer_trust": None})
    pub = r.get("published_overall_risk")
    if r.get("capped") and pub and pub != tier:
        return pub, r.get("trust_basis")
    return None


def apply_trust_cap(cur, apply_writes):
    """Pass every HIGH/CRITICAL registry row through the cap. Returns stats."""
    cur.execute("""select server_id, url, name, risk_tier, metadata
                   from mcp_server_registry where risk_tier in ('HIGH','CRITICAL')""")
    stats = {"checked": 0, "capped": 0}
    now = datetime.datetime.utcnow().isoformat()
    for sid, url, name, tier, meta_raw in cur.fetchall():
        stats["checked"] += 1
        capped = cap_for(url, name, tier)
        if not capped:
            continue
        stats["capped"] += 1
        if not apply_writes:
            continue
        pub, basis = capped
        meta = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["trust_cap"] = {"raw_tier": tier, "trust_basis": basis,
                             "capped_at": now}
        cur.execute("""update mcp_server_registry
                       set risk_tier = %s, metadata = %s where server_id = %s""",
                    (pub, json.dumps(meta), sid))
    return stats


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn-file", required=True,
                    help="file containing the postgres:// DSN (user/pass/db taken from it)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--model-version", default="v3.0_40974559")
    ap.add_argument("--apply", action="store_true", help="actually write (default dry-run)")
    ap.add_argument("--skip-trust-cap", action="store_true",
                    help="ONLY for debugging raw student output; never for publishing")
    return ap.parse_args()


def main():
    import psycopg2
    a = parse_args()
    dsn = open(a.dsn_file).read().strip()
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    if not m:
        print("FATAL: unparseable DSN")
        return 2
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=m.group(3),
                            user=m.group(1), password=m.group(2))
    cur = conn.cursor()

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
    print("[backfill] tier distribution (scored representatives):",
          dict(sorted(dist.items())))

    now = datetime.datetime.utcnow()
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

        # URL-PROPAGATION DELETED (CofC ruling 2026-07-14, FATHER R1).
        # A repo URL is not a server identity: of 11,623 duplicate-URL groups only
        # 332 were true duplicates; 11,291 were DISTINCT SERVERS in one repo.
        # Retro-revert anything wearing a tier it never earned.
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
        cur.execute("select count(*) from mcp_server_registry where server_id = any(%s)",
                    (list(tiers.keys()),))
        print(f"[backfill] DRY-RUN would stamp {cur.fetchone()[0]} rows directly "
              f"(+ URL propagation for the remainder). Re-run with --apply.")

    if a.skip_trust_cap:
        print("[backfill] WARNING trust cap SKIPPED (--skip-trust-cap): "
              "raw tiers MUST NOT be published")
    else:
        st = apply_trust_cap(cur, a.apply)
        print(f"[backfill] trust cap ({'APPLIED' if a.apply else 'DRY-RUN'}):", st)

    if a.apply:
        conn.commit()
    cur.execute("select risk_tier, count(*) from mcp_server_registry group by 1 order by 2 desc")
    print("[backfill] registry tier distribution now:", cur.fetchall())
    return 0


if __name__ == "__main__":
    sys.exit(main())
