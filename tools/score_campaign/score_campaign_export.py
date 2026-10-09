#!/usr/bin/env python3
"""score_campaign_export.py -- select the NEVER-SCORED Fly-PG backlog and emit the
eval_phase2-format JSONL the v3.0_40974559 student consumes.

THE COVERAGE GAP THIS SERVES
----------------------------
`mcp_server_registry` on Fly Postgres (mcplookup-db, ~540k rows) carries ~214k
servers that have never been scored -- risk_tier NULL / '' / 'unassessed' and no
`mcp_llm_axis_scores` row at model_version v3.0_40974559. This exporter selects
that backlog, picks ONE representative per distinct URL (scoring runs on
distinct-URL reps; apply_risk_tier_backfill then propagates the tier to URL
siblings), and writes the exact prompt the student was trained on.

PROMPT = DESCRIPTION-ONLY (train/serve parity, the PR #85 lesson)
-----------------------------------------------------------------
The v3.0 student was SFT'd on a DESCRIPTION-ONLY prompt: a system message
(prompt_system.txt) + a user message of
    MCP SERVER UNDER REVIEW:
      server_id / name / source / url / description
followed by the 7-axis rubric (prompt_signals_block.txt). It has NEVER seen a
tool-manifest, a capability fingerprint, or a provenance block. Adding any of
those at serve time is train/serve skew -- it is exactly why PR #85 is wrong --
so this exporter reads the SAME prompt files weekly_rescore uses and emits
NOTHING else. The two-pole test asserts the emitted prompt carries no such block.

Fly PG is a 1-vCPU / 1GB burstable box that SPILLS on server-side anti-joins and
windows (measured 2026-07-14: both NOT IN and materialized NOT EXISTS ran >7min).
So, exactly like weekly_rescore, we stream two CHEAP scans and do the
distinct-URL / unscored set logic CLIENT-SIDE.

READ-ONLY: this tool only SELECTs from the DB and writes a local JSONL. It never
writes to the database.

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python score_campaign_export.py --dsn-file _dsn.txt --limit 15000 \
        --out wave01.jsonl.gz
    # hermetic:
    python score_campaign_export.py --dsn 'sqlite:///campaign.db' --out out.jsonl
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score_campaign_db import DB  # noqa: E402

MODEL_VERSION = "v3.0_40974559"
# Single source of truth for the prompt: the files the student was trained on and
# weekly_rescore serves. Copying them here would risk drift -> train/serve skew.
PROMPT_DIR = HERE.parent / "rescore"
SYS = (PROMPT_DIR / "prompt_system.txt").read_text(encoding="utf-8")
SIG = (PROMPT_DIR / "prompt_signals_block.txt").read_text(encoding="utf-8")

# Backlog filter, mirroring the task contract. Kept to a cheap single scan (no
# anti-join); the NOT-EXISTS-score and distinct-URL logic is done in Python.
SQL_BACKLOG_KEYS = """
SELECT server_id, COALESCE(url,'') AS url
FROM mcp_server_registry
WHERE description IS NOT NULL AND length(description) > 20
  AND (risk_tier IS NULL OR risk_tier IN ('', 'unassessed'))
"""
SQL_ALL_KEYS = """
SELECT server_id, COALESCE(url,'') AS url
FROM mcp_server_registry
WHERE description IS NOT NULL AND length(description) > 20
"""
SQL_SCORED = f"""
SELECT server_id FROM mcp_llm_axis_scores
WHERE model_version = '{MODEL_VERSION}' AND axis_name = 'overall_risk'
"""
SQL_DETAIL = """
SELECT server_id, name, COALESCE(registry_source, 'remote') AS src,
       COALESCE(url, '') AS url, description
FROM mcp_server_registry
WHERE server_id IN ({ph})
"""


def ukey(sid: str, url: str) -> str:
    return url or sid


def build_user_prompt(sid, name, src, url, descr) -> str:
    """The EXACT description-only header weekly_rescore.ph_export writes, + the
    shared 7-axis rubric. No tool manifest, no fingerprint, no provenance."""
    descr = (descr or "").replace("\n", " ").replace("\r", " ")
    hdr = (f"MCP SERVER UNDER REVIEW:\n  server_id: {sid}\n  name:      {name}\n"
           f"  source:    {src}\n  url:       {url}\n  description: {descr}\n\n")
    return hdr + SIG


def select_backlog(db: DB):
    """Return the ordered list of (sid, url) distinct-URL representatives from the
    never-scored backlog. All set logic client-side (Fly PG spills otherwise)."""
    db.execute(SQL_SCORED)
    scored = {r[0] for r in db.fetchall()}
    # URLs already represented by a scored server -- do not re-score that URL.
    db.execute(SQL_ALL_KEYS)
    scored_urls = {ukey(sid, url) for (sid, url) in db.fetchall() if sid in scored}

    db.execute(SQL_BACKLOG_KEYS)
    cand = [(sid, url) for (sid, url) in db.fetchall()
            if sid not in scored and ukey(sid, url) not in scored_urls]
    # distinct-URL representative: order by (url_key, server_id), keep first.
    cand.sort(key=lambda r: (ukey(r[0], r[1]), r[0]))
    reps, seen = [], set()
    for sid, url in cand:
        k = ukey(sid, url)
        if k in seen:
            continue
        seen.add(k)
        reps.append((sid, url))
    return reps


def fetch_details(db: DB, sids):
    detail = {}
    CHUNK = 900  # stay under sqlite's 999-param limit; pg is unbounded
    for i in range(0, len(sids), CHUNK):
        chunk = sids[i:i + CHUNK]
        ph = ", ".join("%s" for _ in chunk)
        db.execute(SQL_DETAIL.format(ph=ph), tuple(chunk))
        for sid, name, src, url, descr in db.fetchall():
            detail[sid] = (name, src, url, descr)
    return detail


def _open_out(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "wt", encoding="utf-8")
    return open(path, "w", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the never-scored Fly-PG backlog as eval_phase2 JSONL.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dsn", help="DSN inline (sqlite:///... or postgresql://user:pass@host/db)")
    g.add_argument("--dsn-file", help="file containing the DSN")
    ap.add_argument("--host", default="127.0.0.1", help="postgres host (the fly proxy)")
    ap.add_argument("--port", type=int, default=15432, help="postgres port (the fly proxy)")
    ap.add_argument("--limit", type=int, default=0, help="cap this wave (0 = all backlog)")
    ap.add_argument("--out", default="score_campaign_inputs.jsonl",
                    help="output JSONL (.gz enables gzip)")
    a = ap.parse_args()

    dsn = a.dsn if a.dsn else Path(a.dsn_file).read_text(encoding="utf-8").strip()
    db = DB.connect(dsn, host=a.host, port=a.port)
    t0 = time.time()
    reps = select_backlog(db)
    print(f"[export] never-scored distinct-URL backlog: {len(reps)} representatives "
          f"({time.time() - t0:.1f}s)")
    if a.limit and a.limit > 0:
        reps = reps[:a.limit]
        print(f"[export] wave limited to {len(reps)} (--limit {a.limit})")

    sids = [sid for sid, _ in reps]
    detail = fetch_details(db, sids)
    db.close()

    out = Path(a.out)
    written = 0
    with _open_out(out) as f:
        for sid in sids:
            d = detail.get(sid)
            if d is None:
                continue
            name, src, url, descr = d
            rec = {"messages": [{"role": "system", "content": SYS},
                                {"role": "user", "content": build_user_prompt(sid, name, src, url, descr)}],
                   "metadata": {"server_id": sid}}
            f.write(json.dumps(rec) + "\n")
            written += 1
    print(f"[export] wrote {written} eval_phase2 records -> {out}")
    if written == 0:
        print("[export] NOTE: backlog empty -- nothing to score (campaign may be complete).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
