#!/usr/bin/env python3
"""failure_classifier.py -- bucket zo-sentinel failures into the handful of RECURRING
classes we keep re-diagnosing, so each recurrence is RECOGNISED (matched to a class +
counted) in seconds instead of traced from scratch.

The class lives in the log/event SIGNATURE, not a DB column, so this is a signature
pass over the daemon logs (+ optionally mesh_events / gate_quality_state). Pure
`classify_line` is unit-tested; `tally` aggregates; the CLI prints a ranked table and
runs on the tower or folds into pipeline-watch.

    python3 failure_classifier.py                # scan default tower logs (last 4000 lines each)
    python3 failure_classifier.py --since-hours 24
"""
import argparse, collections, glob, os, re, sys

# Ordered: first matching class wins (put the specific before the generic).
# Each class = (name, blurb, [regex signatures]). Patterns are case-insensitive.
CLASSES = [
    ("path_drift",
     "promoter/goose read a directory the other doesn't write (funnel invisible)",
     [r"ALERT path-drift", r"invisible to goose", r"promoted to a folder goose",
      r"lands in a folder goose", r"proposed/ depth \d+ >= cap"]),
    ("novelty_starvation",
     "architect emits no novel work / recycles already-built directives",
     [r"proposed_depth \d+ -> 0 \(\+0\)", r"depth_cap", r"skip already-resolved",
      r"\+0 novel"]),
    ("capacity_429",
     "rate-limit / quota storm (MiniMax daily bucket, free-tier RPM, etc.)",
     [r"\b429\b", r"rate[ _-]?limit", r"Token Plan usage limit", r"backoff exhausted",
      r"quota.*exceeded"]),
    ("key_hydration",
     "ladder shim lost its keys (vault race / hydrate timeout / 502)",
     [r"(?<!no .)RcGeminiAPIKey.{0,15}(unresolved|not set)", r"self-hydrate.*tim(ed|e) ?out",
      r"key_hydrator", r"keys? (unresolved|missing) after"]),
    ("shim_5xx",
     "ladder shim returned 5xx (upstream model error: key race / capacity / timeout)",
     [r'HTTP/1\.1" 50[23]\b', r"\b50[23] (Bad Gateway|Service Unavailable)\b"]),
    ("dup_poison",
     "doubled-prefix / already-built output_file poisons the gate",
     [r"admin_admin", r"doubled[_ -]?prefix", r"output_file_is_sane", r"already built:"]),
    ("publisher_noop_cap",
     "publisher had nothing to commit or hit a daily PR cap",
     [r"nothing to commit", r"artifact already on base", r"deferred_cap",
      r"daily cap \d+ reached"]),
    ("ghost_build",
     "goose reported success but produced no/failed output (ghost)",
     [r"success but output missing", r"ghost[- ]guard", r"GHOST_RETRY",
      r"missing or failed Tier-0"]),
    ("write_service",
     "write_service (:8772) unreachable / heartbeat timeout",
     [r"Heartbeat failed.*8772", r"port=8772.*timed out", r"write_service.*(unreachable|down)"]),
    ("bootstrap_service",
     "a service failed to bind at boot (startup race / port in use)",
     [r"failed \(000", r":87\d\d\b.*\bfailed\b", r"address already in use"]),
]

_COMPILED = [(name, blurb, [re.compile(p, re.I) for p in pats]) for name, blurb, pats in CLASSES]

DEFAULT_LOGS = [
    "proposed_to_pending_promoter", "directive_generator_goose", "goose_runner",
    "ladder_shim", "pr_publisher", "sentinel_registry_api", "sentinel_approval_workflow",
]
LOG_DIRS = ["/home/workspace/logs", "/home/workspace/world_agent/logs"]


def classify_line(line: str):
    """Return the failure-class name for a log line, or None. Pure / no IO."""
    for name, _blurb, pats in _COMPILED:
        if any(p.search(line) for p in pats):
            return name
    return None


def tally(lines):
    """-> (Counter by class, {class: last_example_line})."""
    counts = collections.Counter()
    example = {}
    for ln in lines:
        c = classify_line(ln)
        if c:
            counts[c] += 1
            example[c] = ln.strip()[:160]
    return counts, example


def _iter_log_lines(names, max_lines):
    for name in names:
        path = None
        for d in LOG_DIRS:
            hits = sorted(glob.glob(os.path.join(d, f"{name}*.log")),
                          key=lambda p: os.path.getmtime(p), reverse=True)
            if hits:
                path = hits[0]; break
        if not path:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for ln in f.readlines()[-max_lines:]:
                    yield ln
        except Exception:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-lines", type=int, default=4000, help="tail lines per log")
    ap.add_argument("--logs", default=",".join(DEFAULT_LOGS))
    a = ap.parse_args()
    names = [s.strip() for s in a.logs.split(",") if s.strip()]
    counts, example = tally(_iter_log_lines(names, a.max_lines))
    blurbs = {n: b for n, b, _ in CLASSES}
    print("# zo-sentinel failure-class tally (last %d lines/log)\n" % a.max_lines)
    if not counts:
        print("  no known failure signatures matched -- clean window."); return 0
    print(f"{'count':>6}  {'class':<20} blurb")
    print("-" * 92)
    for name, n in counts.most_common():
        print(f"{n:>6}  {name:<20} {blurbs.get(name,'')}")
        if example.get(name):
            print(f"{'':>6}  {'':<20} e.g. {example[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
