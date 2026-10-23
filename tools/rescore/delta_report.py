#!/usr/bin/env python3
"""delta_report: study score change-over-time; emit SFT + corpus refinement signals.

Consumes score_change_events / score_change_runs (migration 0009) and emits a
JSON report with four sections (chairman directive 2026-07-18: change data must
inform corpus improvements and 7-axis scoring refinement):

  flip_rates     per-axis changed/(changed+unchanged) per run -- rising rate on
                 an axis = scoring instability or real-world drift; either way
                 a retrain/eval trigger.
  transitions    per-axis prev_label -> new_label matrix -- directional drift
                 (e.g. WEAK->UNKNOWN auth_strength churn = extraction weakness,
                 candidate teacher-relabel slice for SFT).
  co_flip        axis-pair co-occurrence of flips on the same server+run
                 (lift over independence) -- which axes move together informs
                 cross-axis inference correlation and schema refinement.
  sft_candidates servers with repeat flips or low-confidence flips
                 (new_p_top < --p-top-floor) -- highest-information examples
                 for the next teacher-student SFT round (sibling repo
                 rob531/zomesh-sentinel-sft).
  corpus_signals instability grouped by registry_source -- a source whose
                 servers flip at elevated rates has weak/mutating metadata:
                 corpus canonicalisation target.

Read-only; safe under THE LINE. Run tower-side via fly proxy like family_count.
"""
import argparse, collections, itertools, json, os, re, sys, time

# FU-151: one shared credential path for every flyctl caller in this repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fly_token import ensure_proxy as _ensure_fly_proxy  # noqa: E402

def pg_conn(dsn_file, port=15432):
    import psycopg2
    # FU-151/FU-133: this used to blind-Popen flyctl with BOTH streams at DEVNULL
    # and sleep(8) without ever checking the port came up, so an unauthenticated
    # flyctl produced a psycopg2 connection error naming nothing. Now the token is
    # hydrated from AgentVault first, stderr is kept, and a failure quotes flyctl's
    # own last line ("no access token available", the 2026-07-28 outage).
    _ensure_fly_proxy(port, "mcplookup-db",
                      os.path.join(os.environ.get("TEMP", "/tmp"),
                                   "_flyctl_proxy_delta_report.err"),
                      log=lambda m: print(m, file=sys.stderr, flush=True))
    dsn = open(dsn_file).read().strip()
    dsn = re.sub(r"@[^/:@]+(:\d+)?/", "@127.0.0.1:%d/" % port, dsn)
    dsn = re.sub(r"host=\S+", "host=127.0.0.1", dsn)
    dsn = re.sub(r"port=\d+", "port=%d" % port, dsn)
    return psycopg2.connect(dsn)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn-file", required=True)
    ap.add_argument("--since-days", type=int, default=90)
    ap.add_argument("--p-top-floor", type=float, default=0.60)
    ap.add_argument("--min-flips", type=int, default=2)
    ap.add_argument("--top-k", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    con = pg_conn(args.dsn_file); cur = con.cursor()
    cur.execute("SET statement_timeout='300s'")
    rpt = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "params": vars(args)}

    cur.execute("select run_id, axis_name, n_new, n_changed, n_unchanged, created_at "
                "from score_change_runs order by created_at")
    rates = collections.defaultdict(dict)
    for run_id, axis, n_new, n_ch, n_un, ts in cur.fetchall():
        denom = (n_ch or 0) + (n_un or 0)
        rates[run_id][axis] = {"new": n_new, "changed": n_ch, "unchanged": n_un,
                               "flip_rate": round((n_ch or 0) / denom, 4) if denom else None,
                               "ts": ts.isoformat() if ts else None}
    rpt["flip_rates"] = rates

    cur.execute("select axis_name, prev_label, new_label, count(*) "
                "from score_change_events where event_ts > now() - interval '%s days' "
                "group by 1,2,3 order by 1,4 desc" % args.since_days)
    trans = collections.defaultdict(dict)
    for axis, a, b, n in cur.fetchall():
        trans[axis]["%s->%s" % (a, b)] = n
    rpt["transitions"] = trans

    cur.execute("select server_id, run_id, array_agg(distinct axis_name) "
                "from score_change_events where event_ts > now() - interval '%s days' "
                "group by 1,2" % args.since_days)
    pair, single, total = collections.Counter(), collections.Counter(), 0
    flips_per_server = collections.Counter()
    for sid, run_id, axes in cur.fetchall():
        total += 1
        flips_per_server[sid] += len(axes)
        for a in axes:
            single[a] += 1
        for a, b in itertools.combinations(sorted(axes), 2):
            pair[(a, b)] += 1
    co = {}
    for (a, b), n in pair.most_common(30):
        exp = single[a] * single[b] / max(total, 1)
        co["%s|%s" % (a, b)] = {"n": n, "lift": round(n / exp, 2) if exp else None}
    rpt["co_flip"] = co

    cur.execute("select e.server_id, count(*) flips, min(e.new_p_top) worst_p, "
                "max(r.registry_source) src, max(r.url) url "
                "from score_change_events e join mcp_server_registry r on r.server_id=e.server_id "
                "where e.event_ts > now() - interval '%s days' "
                "group by 1 having count(*) >= %d or min(e.new_p_top) < %f "
                "order by flips desc, worst_p asc limit %d"
                % (args.since_days, args.min_flips, args.p_top_floor, args.top_k))
    rpt["sft_candidates"] = [
        {"server_id": s, "flips": f, "worst_new_p_top": (round(p, 3) if p is not None else None),
         "source": src, "url": u}
        for s, f, p, src, u in cur.fetchall()]

    cur.execute("select r.registry_source, count(distinct e.server_id) unstable, count(*) events "
                "from score_change_events e join mcp_server_registry r on r.server_id=e.server_id "
                "where e.event_ts > now() - interval '%s days' group by 1 order by 3 desc"
                % args.since_days)
    rpt["corpus_signals"] = [{"source": s, "unstable_servers": u, "events": n}
                             for s, u, n in cur.fetchall()]
    con.close()

    out = json.dumps(rpt, indent=1, default=str)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(out)
        print("wrote %s" % args.out)
    else:
        print(out)

if __name__ == "__main__":
    sys.exit(main())
