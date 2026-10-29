#!/usr/bin/env python3
"""floor_shadow_watch.py -- ONE pass over the ladder-shim capture
(/home/workspace/logs/goose_prompt_response.jsonl) that watches BOTH:
  (A) BUILDER served-model mix -- is routine work landing on the local 3060
      floor (hermes3:8b) / the M3 rung vs cloud? (the floor-fix + M3-trial watch)
  (B) SHADOW-CORPUS health -- capture freshness, directive_id/task fill rate (the
      prompt->build join), and declared(tier)-vs-served(model) mismatch.
They are the SAME per-call records, written at the one shim choke-point, so one
read serves both. Read-only; safe to run anytime / on a schedule.

    python tools/floor_shadow_watch.py            # last 400 shim calls
    WATCH_N=100 python tools/floor_shadow_watch.py
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

LOG = os.environ.get("SHADOW_LOG", "/home/workspace/logs/goose_prompt_response.jsonl")
FAIL = "/home/workspace/logs/shadow_corpus_capture_failures.log"
TOWER_PREFIXES = ("hermes3", "qwen2.5-coder", "gemma4", "phi3", "nomic")


def tail_records(path: str, n: int) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, "rb") as fh:
            data = fh.readlines()[-n:]
    except FileNotFoundError:
        return rows
    for b in data:
        try:
            rows.append(json.loads(b))
        except Exception:
            pass
    return rows


def report(path: str = LOG, n: int = 400, out=print) -> dict:
    rows = tail_records(path, n)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mtime = None
    try:
        mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
    except OSError:
        pass
    served = Counter((r.get("model") or "?") for r in rows)
    declared = Counter((r.get("tier") or "?") for r in rows)
    did = sum(1 for r in rows if r.get("directive_id"))
    tsk = sum(1 for r in rows if r.get("task"))
    tower = sum(1 for r in rows
                if str(r.get("model") or "").startswith(TOWER_PREFIXES))
    try:
        fail_bytes = os.path.getsize(FAIL)
    except OSError:
        fail_bytes = 0

    out("== CAPTURE HEALTH ==")
    out("  now=%s file_mtime=%s rows=%d last_ts=%s failure_log=%dB"
        % (now, mtime, len(rows), (rows[-1].get("ts") if rows else None), fail_bytes))
    out("== (A) served-model mix ==")
    for m, c in served.most_common():
        out("   %5d  %s" % (c, m))
    out("   tower(3060) served: %d / %d" % (tower, len(rows)))
    out("== (B) shadow-corpus joinability ==")
    out("   directive_id non-null: %d / %d" % (did, len(rows)))
    out("   task non-null: %d / %d" % (tsk, len(rows)))
    out("   declared tiers: %s" % dict(declared.most_common(6)))
    return {
        "rows": len(rows), "served": dict(served), "tower_served": tower,
        "directive_id": did, "task": tsk, "last_ts": (rows[-1].get("ts") if rows else None),
        "file_mtime": mtime, "failure_log_bytes": fail_bytes,
    }


def main(argv=None) -> int:
    report(n=int(os.environ.get("WATCH_N", "400")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
