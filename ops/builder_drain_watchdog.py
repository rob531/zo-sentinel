#!/usr/bin/env python3
"""builder_drain_watchdog.py -- deterministic DRAIN/STARVATION guardian for the zo-sentinel builder.

WHY. The chronic builder stall is a DRAIN jam, not a crash. The directive generator
(sentinel_directive_generator_goose) SKIPS every cycle once `directives/proposed/*.json`
reaches DGG_MAX_PROPOSED_DEPTH (cap 20): "proposed/ depth N >= cap; skipping cycle". With no
fresh directives emitted, goose_runner is left with only stale DEFERRED directives in
`pending/` and ships NOTHING. builder_health_checker.py watches the builder log for CRASH
signatures (Cycle error, weights evicted, ...) but NOT this starvation mode, and the daily
`zo-sentinel-pipeline-watch` lane (cron 0 4 * * *) cannot see an intraday drain. On 2026-11-04
this stalled builds for ~6h (last build 09:14Z) before a human noticed. This guardian closes
that gap: it is the starvation half of builder health, run every 15 min beside the crash half.

WHAT. DETERMINISTIC + FAIL-SAFE, mirroring chain_stall_watchdog.py: ONE pass, never raises,
always returns rc 0 so it can never break the cron.
  SIGNAL (STARVED)  = proposed/*.json depth >= cap  AND  nothing has completed to done/ in
                      the last STALL_MINUTES (the generator is capped AND the builder is dry).
  ACTION (AUTO-UNJAM, reversible, $0) = park the OLDEST proposed/*.json beyond KEEP into
                      directives/_parked_drain_<ts>/ (MOVE, not delete; MANIFEST + a one-line
                      revert written), dropping depth below cap so the generator resumes next
                      cycle. Never touches pending/, never deletes, never edits a daemon --
                      the same $0/reversible repair class the pipeline-watch lane already holds.
  LOUD = writes drain_watchdog_result.json (+ a builder_alert.json the existing consumer reads)
         and prints a RED line. A healthy pass clears a stale alert and no-ops.

Usage:
  python builder_drain_watchdog.py           # check; auto-unjam if STARVED
  python builder_drain_watchdog.py --report  # analyse + print, write/park NOTHING (dry)
  python builder_drain_watchdog.py --self-test  # hermetic two-pole ($0, temp dirs)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SENTINEL_DIR = Path(os.environ.get("ZO_SENTINEL_DIR", "/home/workspace/zo_sentinel"))
CAP = int(os.environ.get("DGG_MAX_PROPOSED_DEPTH", "20"))
KEEP = int(os.environ.get("DRAIN_KEEP", str(max(0, CAP - 5))))   # keep newest KEEP < cap -> headroom
STALL_MINUTES = int(os.environ.get("DRAIN_STALL_MINUTES", "90"))
ALERT_TTL_SECONDS = int(os.environ.get("DRAIN_ALERT_TTL", "1800"))


def _proposed_dir(root: Path) -> Path:
    return root / "directives" / "proposed"


def _done_dir(root: Path) -> Path:
    return root / "directives" / "done"


def _json_files(d: Path) -> list[Path]:
    if not d.exists():
        return []
    return [p for p in d.glob("*.json") if ".bak" not in p.name]


def _newest_mtime(d: Path) -> float:
    """Newest *.json mtime in d, or 0.0 if none -- a proxy for 'last build completion'
    (goose_runner moves a built directive into done/)."""
    best = 0.0
    if not d.exists():
        return best
    for p in d.glob("*.json"):
        try:
            m = p.stat().st_mtime
            if m > best:
                best = m
        except OSError:
            continue
    return best


def analyze(root: Path, now: float) -> dict:
    """Pure decision -- no writes. Returns the verdict + the park plan."""
    proposed = sorted(_json_files(_proposed_dir(root)), key=lambda p: p.stat().st_mtime)
    depth = len(proposed)
    done_newest = _newest_mtime(_done_dir(root))
    idle_s = (now - done_newest) if done_newest > 0 else float("inf")
    over_cap = depth >= CAP
    dry = idle_s > STALL_MINUTES * 60
    starved = over_cap and dry
    # park the OLDEST (depth - KEEP) proposals; keep the newest KEEP (freshest, most relevant).
    to_park = proposed[: max(0, depth - KEEP)] if starved else []
    return {
        "starved": starved, "over_cap": over_cap, "dry": dry,
        "proposed_depth": depth, "cap": CAP, "keep": KEEP,
        "idle_minutes": (round(idle_s / 60, 1) if idle_s != float("inf") else None),
        "stall_minutes": STALL_MINUTES,
        "to_park": [p.name for p in to_park],
        "_to_park_paths": to_park,
    }


def park(root: Path, verdict: dict, now: float) -> dict:
    """AUTO-UNJAM: move the oldest over-cap proposals to a timestamped parked dir (reversible)."""
    paths = verdict.get("_to_park_paths") or []
    if not paths:
        return {"parked": 0, "park_dir": None}
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    park_dir = _proposed_dir(root).parent / ("_parked_drain_" + ts)
    park_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for p in paths:
        try:
            shutil.move(str(p), str(park_dir / p.name))
            manifest.append(p.name)
        except OSError as e:  # noqa: PERF203 -- a single move failure is logged, never fatal
            manifest.append("FAILED:%s:%r" % (p.name, e))
    (park_dir / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    revert = "mv %s/*.json %s/" % (park_dir, _proposed_dir(root))
    (park_dir / "REVERT.txt").write_text(revert + "\n", encoding="utf-8")
    return {"parked": len([m for m in manifest if not m.startswith("FAILED")]),
            "park_dir": str(park_dir), "revert": revert}


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run(root: Path, now: float, *, report: bool) -> dict:
    v = analyze(root, now)
    result = {k: val for k, val in v.items() if not k.startswith("_")}
    result["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    result["mode"] = "report" if report else "act"
    if v["starved"] and not report:
        result["unjam"] = park(root, v, now)
        print("[drain-watchdog] RED: STARVED (proposed depth %d >= cap %d, idle %sm > %dm) "
              "-> AUTO-UNJAMMED: parked %d oldest proposals (reversible: %s)" % (
                  v["proposed_depth"], v["cap"], result.get("idle_minutes"), STALL_MINUTES,
                  result["unjam"]["parked"], result["unjam"].get("revert")))
    elif v["starved"]:
        print("[drain-watchdog] RED (report-only): STARVED -- would park %d oldest proposals" % len(v["to_park"]))
    else:
        reason = ("depth %d < cap %d" % (v["proposed_depth"], v["cap"])) if not v["over_cap"] \
                 else ("built recently (idle %sm <= %dm)" % (result.get("idle_minutes"), STALL_MINUTES))
        print("[drain-watchdog] ok: builder draining (%s)" % reason)
    # LOUD artifacts (best-effort; never fatal).
    try:
        out = root / "shared" / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out / "drain_watchdog_result.json", result)
        alert = out / "builder_alert.json"
        if v["starved"] and not report:
            _atomic_write_json(alert, {"source": "builder_drain_watchdog", "kind": "drain_starvation",
                                       "action": "auto_unjammed", "at": result["at"], "detail": result})
        elif alert.exists():
            # clear a stale drain alert once healthy + past TTL (leave fresh ones for the consumer).
            try:
                a = json.loads(alert.read_text(encoding="utf-8"))
                if a.get("source") == "builder_drain_watchdog" and (now - os.path.getmtime(alert)) > ALERT_TTL_SECONDS:
                    alert.unlink()
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return result


# ---------------------------------------------------------------------------
# TWO-POLE SELF-TEST -- hermetic (temp dirs, injected clock). $0.
# ---------------------------------------------------------------------------
def self_test() -> int:
    import tempfile
    fails = []

    def ok(c, m):
        print(("  PASS " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    def mk(root, rel, n, mtime):
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            p = d / ("d%03d.json" % i)
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (mtime, mtime))

    now = 2_000_000_000.0
    old = now - 3 * 3600      # 3h ago (past the 90m stall)
    fresh = now - 10 * 60     # 10m ago

    # POLE-A (STARVED): proposed over cap + done/ stale -> unjam parks down to KEEP.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mk(root, "directives/proposed", CAP + 12, old)   # over cap
        mk(root, "directives/done", 3, old)              # nothing built recently
        res = run(root, now, report=False)
        ok(res["starved"] is True, "A STARVED detected (depth>=cap AND dry)")
        remaining = len(list((root / "directives" / "proposed").glob("*.json")))
        ok(remaining == KEEP, "A auto-unjam parked down to KEEP=%d (left %d)" % (KEEP, remaining))
        ok(res["unjam"]["parked"] == (CAP + 12 - KEEP), "A parked exactly the over-KEEP oldest")
        parked_dirs = list((root / "directives").glob("_parked_drain_*"))
        ok(len(parked_dirs) == 1 and (parked_dirs[0] / "MANIFEST.txt").exists()
           and (parked_dirs[0] / "REVERT.txt").exists(), "A park is reversible (manifest + revert written)")

    # POLE-B.1 (HEALTHY-under-cap): depth < cap -> NO unjam.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mk(root, "directives/proposed", CAP - 3, old)
        mk(root, "directives/done", 3, old)
        res = run(root, now, report=False)
        ok(res["starved"] is False and not list((root / "directives").glob("_parked_drain_*")),
           "B.1 under cap -> NOT starved, parks nothing")

    # POLE-B.2 (ANTI-RUBBER-STAMP): over cap BUT built recently -> NOT starved (don't unjam a busy builder).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mk(root, "directives/proposed", CAP + 12, old)
        mk(root, "directives/done", 3, fresh)            # a build completed 10m ago
        res = run(root, now, report=False)
        ok(res["over_cap"] is True and res["starved"] is False,
           "B.2 over cap BUT recently built -> NOT starved (anti-rubber-stamp; backpressure is healthy)")
        ok(not list((root / "directives").glob("_parked_drain_*")), "B.2 parked nothing (builder is working)")

    # POLE-B.3 (REPORT dry-run): starved but --report -> detects, parks NOTHING.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mk(root, "directives/proposed", CAP + 12, old)
        mk(root, "directives/done", 3, old)
        res = run(root, now, report=True)
        ok(res["starved"] is True and not list((root / "directives").glob("_parked_drain_*")),
           "B.3 --report detects STARVED but parks nothing (pure)")

    print("RESULT: " + ("ALL PASS" if not fails else "FAIL: " + "; ".join(fails)))
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="deterministic drain/starvation guardian for the zo-sentinel builder")
    ap.add_argument("--report", action="store_true", help="analyse + print, write/park NOTHING")
    ap.add_argument("--daemon", action="store_true",
                    help="loop forever: one pass every DRAIN_POLL_SECS (default 900). This is the "
                         "durable form on ZoComputer -- go.sh launches it under daemon_wrapper.sh "
                         "(crontabs here can be inert after a cold boot; daemons are not).")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    ap.add_argument("--sentinel-dir", default=None, help="override ZO_SENTINEL_DIR (for tests)")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    root = Path(a.sentinel_dir) if a.sentinel_dir else SENTINEL_DIR
    poll = int(os.environ.get("DRAIN_POLL_SECS", "900"))
    while True:
        try:
            run(root, time.time(), report=a.report)
        except Exception as e:  # noqa: BLE001 -- FAIL-SAFE: a pass never kills the daemon/cron (rc0 ethos)
            print("[drain-watchdog] ERROR (non-fatal): %r" % e)
        if not a.daemon:
            break
        time.sleep(poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
