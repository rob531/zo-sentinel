#!/usr/bin/env python3
"""
ecosystems_enrichment_adapter.py  -- Commit A companion

Reads mcp_ecosystems_metadata (populated by ecosystems_metadata_fetcher)
and produces community_signal + temporal_stability enrichment records
that match the existing mcp_signal_enrichments schema.

Why separate from the fetcher: the fetcher deals with the ecosyste.ms
API and caching concerns. The adapter deals with scoring concerns. Nice
separation so scoring changes don't require re-fetching, and vice versa.

Output table: mcp_signal_enrichments (existing)
Enrichment names produced:
  - community_signal_enrichment  (enrichment of the existing one, via same name)
  - temporal_stability_enrichment

Scoring logic:

  COMMUNITY SIGNAL (downloads-driven):
    Uses log10(downloads) to map the 0-to-100M+ range onto 0-100:
      downloads <  100        -> score 10   (essentially unpublished)
      downloads <  1,000      -> score 30
      downloads <  10,000     -> score 55
      downloads <  100,000    -> score 75
      downloads <  1,000,000  -> score 88
      downloads >= 1,000,000  -> score 95
      downloads >= 10,000,000 -> score 98
    Modifiers:
      + 2 if ecosystems_observed has >= 2 distinct ecosystems (cross-registry presence)
      + 1 if top_ecosystem is 'npm' or 'pypi' (vs niche ecosystems)
    Cap at 100.

  TEMPORAL STABILITY (age-driven):
    Uses age_days_estimate:
      no data        -> score 40   (unknown but not zero)
      < 30 days      -> score 45   (very new, unproven)
      < 180 days     -> score 65
      < 365 days     -> score 80
      < 730 days     -> score 90
      >= 730 days    -> score 95

Evidence payload includes all inputs so signal_bridge can reason about
whether to override signal_analyser's flat defaults.

Run modes:
  python3 ecosystems_enrichment_adapter.py --once   # one pass, write enrichments, exit
  python3 ecosystems_enrichment_adapter.py --loop   # daemon, refresh every 1h

For commit A, we run --once from the builder/cron initially, then decide
if daemon mode is worth it after watching it operate.
"""
import hashlib
import json
import logging
import math
import os
import signal as pysignal
import sys
import time
from datetime import datetime, timezone

import requests

SERVICE_NAME = "ecosystems_enrichment_adapter"
WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL   = f"{WRITE_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"
WRITE_URL   = f"{WRITE_SERVICE}/write"

LOOP_INTERVAL_S = 3600  # 1 hour in --loop mode

log = logging.getLogger(SERVICE_NAME)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

_shutdown = False


def ws_query(sql: str, params: list = None) -> list:
    r = requests.post(
        QUERY_URL,
        json={"sql": sql, "params": params or [], "limit": 10000},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("rows", []) if isinstance(body, dict) else []


def ws_write(row: dict) -> bool:
    try:
        r = requests.post(
            WRITE_URL,
            json={"table": "mcp_signal_enrichments", "rows": row,
                  "mode": "upsert", "agent_id": SERVICE_NAME, "wait": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning("ws_write failed: %s", e)
        return False


# ---- Scoring functions -----------------------------------------------

def score_community_signal(row: dict) -> tuple:
    """Returns (score, evidence_dict)."""
    downloads = row.get("top_downloads") or 0
    ecosystems_raw = row.get("ecosystems_observed") or "[]"
    try:
        ecosystems = json.loads(ecosystems_raw) if isinstance(ecosystems_raw, str) else ecosystems_raw
    except Exception:
        ecosystems = []
    top_ecosystem = (row.get("top_ecosystem") or "").lower()

    if downloads >= 10_000_000: base = 98.0
    elif downloads >= 1_000_000: base = 95.0
    elif downloads >= 100_000:   base = 88.0
    elif downloads >= 10_000:    base = 75.0
    elif downloads >= 1_000:     base = 55.0
    elif downloads >= 100:       base = 30.0
    elif downloads > 0:          base = 15.0
    else:                        base = 10.0

    modifiers = 0.0
    if len(set(ecosystems)) >= 2:
        modifiers += 2.0
    if top_ecosystem in ("npm", "pypi"):
        modifiers += 1.0

    score = min(100.0, base + modifiers)
    evidence = {
        "source": "ecosystems_metadata",
        "downloads": downloads,
        "ecosystems_observed": list(set(ecosystems)),
        "top_ecosystem": top_ecosystem,
        "base_score": base,
        "modifiers": modifiers,
        "final_score": score,
    }
    return score, evidence


def score_temporal_stability(row: dict) -> tuple:
    """Returns (score, evidence_dict)."""
    age_days = row.get("age_days_estimate")

    if age_days is None:      score = 40.0; band = "unknown"
    elif age_days < 30:       score = 45.0; band = "very_new"
    elif age_days < 180:      score = 65.0; band = "young"
    elif age_days < 365:      score = 80.0; band = "established"
    elif age_days < 730:      score = 90.0; band = "mature"
    else:                     score = 95.0; band = "very_mature"

    evidence = {
        "source": "ecosystems_metadata",
        "age_days": age_days,
        "band": band,
        "final_score": score,
    }
    return score, evidence


# ---- Main logic ------------------------------------------------------

# Generated once per adapter invocation so each --once run produces a
# coherent batch of enrichments sharing a run_id. signal_bridge reads
# most-recent per (server_id, enrichment_name) via ORDER BY computed_at
# DESC, so multiple runs accumulate cleanly without upsert semantics.
_RUN_ID = f"ecosystems_adapter_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"


def _write_enrichment(server_id: str, enrichment_name: str,
                      score: float, evidence: dict) -> bool:
    """Insert new enrichment row. Does NOT upsert -- each run creates a
    fresh row with this adapter instance's _RUN_ID. signal_bridge picks
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
    return ws_write(row)


def process_all() -> dict:
    """One pass over mcp_ecosystems_metadata, write enrichments.
    Returns count summary."""
    counts = {"total": 0, "community_written": 0, "temporal_written": 0,
              "skipped_no_data": 0, "write_failed": 0}
    try:
        rows = ws_query(
            "SELECT server_id, top_downloads, top_ecosystem, "
            "ecosystems_observed, age_days_estimate, lookup_status "
            "FROM mcp_ecosystems_metadata WHERE lookup_status = 'ok'"
        )
    except Exception as e:
        log.error("query failed: %s", e)
        return counts

    counts["total"] = len(rows)
    log.info("scoring %d servers from mcp_ecosystems_metadata", len(rows))

    for r in rows:
        if _shutdown:
            break
        server_id = r["server_id"]

        cs_score, cs_evidence = score_community_signal(r)
        if _write_enrichment(server_id, "community_signal_enrichment",
                             cs_score, cs_evidence):
            counts["community_written"] += 1
        else:
            counts["write_failed"] += 1

        ts_score, ts_evidence = score_temporal_stability(r)
        if _write_enrichment(server_id, "temporal_stability_enrichment",
                             ts_score, ts_evidence):
            counts["temporal_written"] += 1
        else:
            counts["write_failed"] += 1

    return counts


def _shutdown_handler(_signum, _frame):
    global _shutdown
    _shutdown = True


def main():
    pysignal.signal(pysignal.SIGTERM, _shutdown_handler)
    pysignal.signal(pysignal.SIGINT, _shutdown_handler)

    mode = "--once"
    if "--loop" in sys.argv:
        mode = "--loop"

    log.info("=" * 60)
    log.info("ZO-SENTINEL Ecosystems Enrichment Adapter v1.0 (Commit A)")
    log.info("  Mode: %s", mode)
    if mode == "--loop":
        log.info("  Interval: %ds", LOOP_INTERVAL_S)
    log.info("=" * 60)

    counts = process_all()
    log.info("first pass: %s", counts)

    if mode == "--once":
        return 0

    while not _shutdown:
        time.sleep(LOOP_INTERVAL_S)
        if _shutdown:
            break
        counts = process_all()
        log.info("cycle: %s", counts)

    log.info("ecosystems_enrichment_adapter clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())