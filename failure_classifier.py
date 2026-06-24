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
    ("no_novel_builds",
     "REGRESSION: architect emits +0 / recycles built work -- the graphify-native novelty design (DESIGN_graph_native_feedback.md) not delivering",
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
    ("publisher_watermark_frozen",
     "REGRESSION: publisher watermark write dropped by write_service -> frozen -> re-scans stale backlog, NO new PRs",
     [r"WARN: durable write", r"watermark never persisted", r"state file write failed"]),
    ("bootstrap_service",
     "a service failed to bind at boot (startup race / port in use)",
     [r"failed \(000", r":87\d\d\b.*\bfailed\b", r"address already in use"]),
]

_COMPILED = [(name, blurb, [re.compile(p, re.I) for p in pats]) for name, blurb, pats in CLASSES]

# Seeded from DESIGN_graph_native_feedback.md "Regression caveats -- MUST hold" (the
# pre-existing playbook, June 2026) + the fixes verified 2026-06-20/21. The
# false_positive field is the highest-value bit: it stops the symptom-chase.
PLAYBOOK = {
    "no_novel_builds": {
        "root": "architect proposes +0 or re-proposes already-built work; the graphify "
                "structural-context edge was meant to prevent this.",
        "false_positive": "PROVEN FALSE-POSITIVE TRAP: +0 is usually the architect's MODEL "
                "PATH failing, not a generation bug. CHECK ladder_shim.log for 502s/timeouts "
                "FIRST -- if sick, it's capacity_429/key_hydration, NOT this. Also: GOOSE_MODEL "
                "is pinned to MiniMax-Text-01 and the Phase-5 escalation edge (ZO_ESCALATE) is "
                "default OFF, so the architect may be stuck on one rung. (Re-confirmed 2026-06-21: "
                "shim=200 OK -> model path healthy -> residual +0/dupes are real.)",
        "fix": "verify shim 200s; then bridge done-dedup (#355) + recency avoid-list (#349). "
               "Deeper: turn on matrix-driven rung selection (documented follow-on).",
        "refs": [333, 349, 355],
    },
    "path_drift": {
        "root": "promoter/goose read a different directives dir than the writer; or git "
                "reset --hard wipes untracked runtime state on host refresh.",
        "false_positive": "scanned=0 alone is ambiguous (could be genuinely idle). Confirm via "
                "the ALERT path-drift tripwire or proposals existing elsewhere.",
        "fix": "canonical absolute path + tripwire (#347); keep runtime state UNTRACKED so "
               "git reset --hard leaves it (DESIGN doc caveat).",
        "refs": [347],
    },
    "write_service": {
        "root": "write_service is a SINGLE DuckDB connection (PR #35, code-134 crash if a 2nd "
                "writer is added); zo_db_query under load destabilizes it.",
        "false_positive": "transient 8772 heartbeat timeouts during zm-go bootstrap are normal "
                "(service warming) -- not a fault unless sustained.",
        "fix": "never add a second writer; keep hot paths file-based / batched through :8772. "
               "Schema-mismatch spam (e.g. mcp_threat_associations ON CONFLICT) = a bad upsert "
               "writer to fix.",
        "refs": [],
    },
    "ghost_build": {
        "root": "goose reports success but writes no file (tool-call didn't land); the Tier-1 "
                "import gate also false-FAILs on host-only deps.",
        "false_positive": "completion gates on Tier-0 SYNTAX only; Tier-1 is advisory -- a Tier-1 "
                "'fail' is not a real build failure.",
        "fix": "the shim fallback writes the file; deeper fix is goose tool-call reliability + a "
               "capable default model (bake-off: Cerebras/Groq).",
        "refs": [251],
    },
    "capacity_429": {"root": "rate-limit/quota storm (MiniMax daily bucket, free-tier RPM).",
        "false_positive": "MiniMax 429 is EXPECTED (paid daily bucket) -- it parks and auto-recovers; "
                "don't demote it.", "fix": "quota-aware failover + park/recover (#343); capacity rungs.",
        "refs": [343, 345]},
    "key_hydration": {"root": "shim lost keys (vault race / hydrate timeout / GNOME keyring bug).",
        "false_positive": "the relaunch SUCCESS line contains 'no RcGeminiAPIKey unresolved' -- not a fault.",
        "fix": "env-var key load (keyring bypass) + substring loader (#337); relaunch_ladder_keyed.",
        "refs": [265, 337]},
    "publisher_noop_cap": {"root": "nothing committable (edit no-op) or daily PR cap.",
        "false_positive": "repeated 'nothing to commit' on the SAME artifact = healthy idle. BUT repeated "
                "no-ops across MANY DIFFERENT OLD artifacts while NEW build_artifacts pile up = the watermark "
                "is FROZEN (see publisher_watermark_frozen), NOT idle -- check watermark age first.",
        "fix": "cap raised to 100 (#294, public repo); supply creation-class work.", "refs": [294]},
    "dup_poison": {"root": "doubled-prefix / already-built output_file poisons the Tier-0 gate.",
        "false_positive": "", "fix": "output_file sanity gate (#279) + durable quarantine (#334).",
        "refs": [279, 334]},
    "shim_5xx": {"root": "shim upstream model error (key race / capacity / timeout).",
        "false_positive": "a single transient 502 during failover is normal; sustained 5xx is the signal.",
        "fix": "see capacity_429 / key_hydration.", "refs": [343]},
    "bootstrap_service": {"root": "a service failed to bind at boot (startup race / port in use).",
        "false_positive": "the 12.10 health-check 000 during zm-go is a TIMING race -- the service "
                "usually binds a second later (registry_api log shows clean Uvicorn startup).",
        "fix": "startup-retry/ordering so the health-check doesn't false-red.", "refs": []},
    "publisher_watermark_frozen": {
        "root": "write_service (:8772) drops the publisher's watermark write (store.write -> False); "
                "with state only in mesh_memory the watermark freezes and the publisher re-scans the "
                "same stale window forever, head-of-line-blocking every new PR (2026-06-23).",
        "false_positive": "",
        "fix": "publisher state (watermark/dedup/budget) moved to a local durable file "
               "(PR_PUBLISHER_STATE_FILE) so a dropped write can't freeze it; seed the file forward.",
        "refs": []},
}


def playbook(class_name):
    """Return {root, false_positive, fix, refs} for a failure class, or {}."""
    return PLAYBOOK.get(class_name, {})

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


def watermark_staleness_note():
    """publisher_watermark_frozen is DB/FILE state, not a log line -- probe the
    publisher's local watermark file and flag it if stale (> 2h), so a frozen
    watermark is RECOGNISED, not dismissed as publisher_noop_cap idle."""
    import json as _j, datetime as _dt, os as _os
    p = _os.environ.get("PR_PUBLISHER_STATE_FILE", "/home/workspace/.pr_publisher_state.json")
    try:
        wm = _j.loads(open(p, encoding="utf-8").read()).get("watermark")
        if not wm:
            return None
        age = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.datetime.fromisoformat(str(wm).replace("Z", "+00:00"))).total_seconds() / 3600.0
        if age > 2:
            return f"publisher watermark {age:.1f}h STALE ({wm}) -> publisher_watermark_frozen (NOT idle)"
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-lines", type=int, default=4000, help="tail lines per log")
    ap.add_argument("--logs", default=",".join(DEFAULT_LOGS))
    a = ap.parse_args()
    _wmnote = watermark_staleness_note()
    if _wmnote:
        print(f"!! {_wmnote}\n")
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
        pb = PLAYBOOK.get(name)
        if pb:
            if pb.get("false_positive"):
                print(f"{'':>6}  {'':<20} CHECK FIRST: {pb['false_positive'][:140]}")
            print(f"{'':>6}  {'':<20} fix: {pb.get('fix','')[:120]}  refs={pb.get('refs',[])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
