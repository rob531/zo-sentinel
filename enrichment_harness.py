#!/usr/bin/env python3
"""
enrichment_harness.py -- Generic runner that proves (or disproves) an enrichment.

Usage:
    python3 enrichment_harness.py \\
        --enrichment /home/workspace/zo_sentinel/supply_chain_enrichment.py \\
        --runs 3 \\
        --sample-size 20

What it does:
    1. Dynamically imports the enrichment module.
    2. Verifies it exposes compute_score(metadata: dict) -> (float, dict).
    3. Generates N runs of synthetic MCP metadata with varied inputs.
    4. Calls compute_score(metadata) for each synthetic MCP in each run.
    5. Writes results to mcp_signal_enrichments with run_id and input_fingerprint.

Invariants enforced:
    - Enrichment output must be a float in [0, 100]
    - Enrichment must be deterministic (same metadata -> same score)
    - Enrichment must not raise on any metadata combination
    - Enrichment must complete each call in < 2s
"""
import argparse
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from itertools import product
from pathlib import Path
from typing import Any, Callable

import requests

WS = "http://127.0.0.1:8772"

REGISTRY_SOURCES = ["npm_official", "github", "smithery", "pypi"]
AGE_DAYS         = [1, 30, 180, 365, 1825]
DOWNLOAD_COUNTS  = [0, 10, 1000, 100000, 10000000]
DEP_COUNTS       = [0, 1, 5, 20, 100]
PUBLISHER_STATES = [True, False]
STAR_COUNTS      = [0, 5, 100, 1000, 50000]


def load_enrichment(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("enrichment not found: " + path)
    enrichment_name = p.stem
    spec = importlib.util.spec_from_file_location(enrichment_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load spec for " + path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "compute_score", None)
    if fn is None or not callable(fn):
        raise AttributeError(path + " must expose compute_score(metadata) -> (float, dict)")
    return enrichment_name, fn


def synthetic_mcps(n, seed):
    all_combos = list(product(
        REGISTRY_SOURCES, AGE_DAYS, DOWNLOAD_COUNTS,
        DEP_COUNTS, PUBLISHER_STATES, STAR_COUNTS,
    ))
    stride = (seed * 7 + 13) % len(all_combos)
    rotated = all_combos[stride:] + all_combos[:stride]
    combos = rotated[:n]
    mcps = []
    for i, (src, age, dl, deps, verified, stars) in enumerate(combos):
        mcps.append({
            "server_id":          "__harness_" + str(seed).zfill(2) + "_" + str(i).zfill(4) + "__",
            "registry_source":    src,
            "age_days":           age,
            "download_count":     dl,
            "dependency_count":   deps,
            "publisher_verified": verified,
            "stars":              stars,
        })
    return mcps


def fingerprint(metadata):
    features = {k: v for k, v in metadata.items() if k != "server_id"}
    s = json.dumps(features, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def check_determinism(fn, metadata):
    s1, _ = fn(dict(metadata))
    s2, _ = fn(dict(metadata))
    if s1 != s2:
        raise ValueError("enrichment not deterministic: " + str(s1) + " != " + str(s2))


def check_range(score):
    if not isinstance(score, (int, float)):
        raise TypeError("score must be numeric, got " + type(score).__name__)
    score = float(score)
    if not (0.0 <= score <= 100.0):
        raise ValueError("score out of [0,100]: " + str(score))
    return score


def ws_write_batch(rows):
    if not rows:
        return 0
    try:
        r = requests.post(
            WS + "/write",
            json={
                "table":    "mcp_signal_enrichments",
                "rows":     rows,
                "mode":     "upsert",
                "agent_id": "enrichment_harness",
                "wait":     True,
            },
            timeout=30,
        )
        if r.status_code == 200:
            return len(rows)
        print("[FAIL] write: HTTP " + str(r.status_code) + " " + r.text[:200])
        return 0
    except Exception as e:
        print("[ERR] write: " + str(e))
        return 0


def run_harness(enrichment_path, runs, sample_size):
    print("\n=== harness: " + enrichment_path + " ===")
    print("    runs=" + str(runs) + "  sample_size=" + str(sample_size))

    try:
        enrichment_name, fn = load_enrichment(enrichment_path)
    except Exception as e:
        print("[FAIL] load: " + str(e))
        return 1
    print("[OK] loaded enrichment: " + enrichment_name)

    total_written = 0
    all_scores = []

    for run_idx in range(runs):
        run_id = "harness_" + uuid.uuid4().hex[:12]
        seed  = run_idx + 1
        mcps  = synthetic_mcps(sample_size, seed)
        print("\n  run " + str(run_idx+1) + "/" + str(runs) + "  run_id=" + run_id + "  seed=" + str(seed))

        rows = []
        run_scores = []
        for mcp in mcps:
            t0 = time.monotonic()
            try:
                score_raw, evidence = fn(dict(mcp))
            except Exception as e:
                print("    [FAIL] compute_score raised on " + mcp["server_id"] + ": " + str(e))
                return 2
            dur = time.monotonic() - t0
            if dur > 2.0:
                print("    [FAIL] compute_score took " + str(round(dur, 2)) + "s (>2s)")
                return 2
            try:
                score = check_range(score_raw)
            except Exception as e:
                print("    [FAIL] " + str(e))
                return 2
            if len(run_scores) < 3:
                try:
                    check_determinism(fn, mcp)
                except Exception as e:
                    print("    [FAIL] " + str(e))
                    return 2
            rows.append({
                "run_id":            run_id,
                "enrichment_name":   enrichment_name,
                "server_id":         mcp["server_id"],
                "score":             score,
                "evidence":          json.dumps(evidence) if isinstance(evidence, (dict, list)) else str(evidence),
                "input_fingerprint": fingerprint(mcp),
            })
            run_scores.append(score)

        written = ws_write_batch(rows)
        total_written += written
        distinct = len(set(run_scores))
        print("    scores: min=" + str(round(min(run_scores),1)) + " max=" + str(round(max(run_scores),1)) + " distinct=" + str(distinct) + "/" + str(len(run_scores)) + "  written=" + str(written))
        all_scores.extend(run_scores)

    print("\n[OK] total written: " + str(total_written))
    print("     overall distinct: " + str(len(set(all_scores))) + "/" + str(len(all_scores)))
    print("\nNext: run enrichment_evidence.sql to see the verdict.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Enrichment harness")
    ap.add_argument("--enrichment", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--sample-size", type=int, default=20)
    args = ap.parse_args()
    if args.runs < 1 or args.runs > 10:
        print("--runs must be 1..10")
        return 1
    if args.sample_size < 5 or args.sample_size > 200:
        print("--sample-size must be 5..200")
        return 1
    return run_harness(args.enrichment, args.runs, args.sample_size)


if __name__ == "__main__":
    sys.exit(main())