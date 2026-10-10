#!/usr/bin/env python3
"""tools/score_deterministic/score_deterministic_run.py -- batch the deterministic
scorer over the Fly-PG NEVER-SCORED backlog.

Selection mirrors the canonical never-scored anti-join (never_scored_backlog_api /
score_campaign_export): registry rows with a description that have NO row in
`mcp_llm_axis_scores`. For each, score the 7 axes via the deterministic backend,
UPSERT the 7 rows into mcp_llm_axis_scores (ON CONFLICT (server_id, axis_name,
model_version) -> update, so re-runs are idempotent), and stamp
mcp_server_registry.risk_tier via the canonical gate_rule_v1 + trust_gate.

DEFAULT IS DRY-RUN. Pass --apply to write. DSN is a parameter; no secrets in code.

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python tools/score_deterministic/score_deterministic_run.py --dsn-file <path> \
        [--backend glide|jev] [--limit 3141] [--apply]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

try:
    from .det_scorer import make_backend, score_server, tier_from_rows
except ImportError:  # run directly
    from det_scorer import make_backend, score_server, tier_from_rows  # type: ignore

UPSERT_SQL = """
insert into mcp_llm_axis_scores
  (server_id, axis_name, label, label_index, probs, p_top, p_critical, p_danger,
   escalated, escalated_to, decision_rule_version, model_version, adapter_sha256,
   scored_at)
values
  (%(server_id)s, %(axis_name)s, %(label)s, %(label_index)s, %(probs)s, %(p_top)s,
   %(p_critical)s, %(p_danger)s, %(escalated)s, %(escalated_to)s,
   %(decision_rule_version)s, %(model_version)s, %(adapter_sha256)s, %(scored_at)s)
on conflict (server_id, axis_name, model_version) do update set
  label = excluded.label, label_index = excluded.label_index, probs = excluded.probs,
  p_top = excluded.p_top, p_critical = excluded.p_critical,
  p_danger = excluded.p_danger, escalated = excluded.escalated,
  escalated_to = excluded.escalated_to,
  decision_rule_version = excluded.decision_rule_version,
  adapter_sha256 = excluded.adapter_sha256, scored_at = excluded.scored_at
"""

SELECT_BACKLOG_SQL = """
select r.server_id, r.name, r.url, r.registry_source, r.description
from mcp_server_registry r
where r.description is not null and r.description <> ''
  and not exists (
    select 1 from mcp_llm_axis_scores s where s.server_id = r.server_id)
order by r.first_seen asc
limit %s
"""


def _parse_dsn(dsn: str) -> Tuple[str, str, str]:
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    if not m:
        raise SystemExit("FATAL: unparseable DSN")
    return m.group(1), m.group(2), m.group(3)


def row_to_params(row: Dict) -> Dict:
    """Shape a scorer row into the UPSERT params (probs -> JSON text)."""
    p = dict(row)
    p["probs"] = json.dumps(row.get("probs"))
    return p


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Batch deterministic scorer over the backlog.")
    ap.add_argument("--dsn-file", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--backend", choices=("glide", "jev"), default="glide")
    ap.add_argument("--limit", type=int, default=3141,
                    help="max never-scored servers to process (default: the backlog)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--skip-tier", action="store_true",
                    help="only write axis rows; do not stamp registry.risk_tier")
    ap.add_argument("--api-key-env", default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    import psycopg2

    a = parse_args(argv)
    user, pw, db = _parse_dsn(open(a.dsn_file).read().strip())
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=db, user=user, password=pw)
    cur = conn.cursor()

    cur.execute(SELECT_BACKLOG_SQL, (a.limit,))
    backlog = [{"server_id": sid, "name": name, "url": url, "source": source,
                "description": desc}
               for sid, name, url, source, desc in cur.fetchall()]
    print(f"[run] never-scored backlog selected: {len(backlog)} "
          f"(backend={a.backend}, apply={a.apply})")
    if not backlog:
        print("[run] nothing to do")
        return 0

    key_env = a.api_key_env or ("FASTINO_API_KEY" if a.backend == "glide" else "JEV_API_KEY")
    backend = make_backend(a.backend, api_key=os.environ.get(key_env, ""))

    now = datetime.datetime.utcnow()
    scored = axis_rows = tier_writes = errors = 0
    tier_dist: Dict[str, int] = {}
    for i, srv in enumerate(backlog, 1):
        try:
            rows = score_server(srv, backend, now_iso=now.isoformat())
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"[run] score error on {srv['server_id']}: {e}")
            continue
        scored += 1
        tier = tier_from_rows(srv, rows)
        tier_dist[tier["published_tier"]] = tier_dist.get(tier["published_tier"], 0) + 1
        if a.apply:
            for row in rows:
                cur.execute(UPSERT_SQL, row_to_params(row))
                axis_rows += 1
            if not a.skip_tier:
                cur.execute(
                    "update mcp_server_registry set risk_tier=%s, last_assessed=%s "
                    "where server_id=%s",
                    (tier["published_tier"], now, srv["server_id"]))
                tier_writes += cur.rowcount
        if i % 100 == 0:
            print(f"[run] processed {i}/{len(backlog)}")
            if a.apply:
                conn.commit()

    if a.apply:
        conn.commit()
        print(f"[run] APPLIED axis_rows={axis_rows} tier_writes={tier_writes} "
              f"scored={scored} errors={errors}")
    else:
        print(f"[run] DRY-RUN would upsert {scored*7} axis rows for {scored} servers "
              f"(+ {scored} tier stamps). errors={errors}. Re-run with --apply.")
    print("[run] published-tier distribution:", dict(sorted(tier_dist.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
