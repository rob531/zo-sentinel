#!/usr/bin/env python3
"""queue_census.py -- the organ that reads the POPULATION, not the members.

WHY THIS EXISTS (2026-07-29/30)
-------------------------------
Every self-inspecting organ this loop has reads one of two surfaces:

    generate_spine / pull_check / reachability ratchet    -> the WORKING TREE
    loop_watch / mesh_events / FU ledger / graphify KL    -> ITS OWN RECORDS

Neither can see the open queue. The working tree is DOWNSTREAM OF THE GATE, so it
structurally cannot show a defect the gate is currently catching -- a healthy gate
in front of a broken emitter looks exactly like a healthy system. On 2026-07-29 the
disk read 133/140 manifests clean while ALL 36 open manifest PRs were invalid, and
51 of the 68 open PRs had been opened in the preceding 24 hours.

`pr_triage` was the one organ that could have seen it: it parses every manifest
diff and knew valid from invalid. It spent that verdict on a per-PR LABEL and never
asked what fraction of the lane was valid. The information existed. Nothing
aggregated it.

Then on 2026-07-30 the complementary failure: builds kept being EMITTED (#2397 was
built 23:06) while nothing MERGED for 10.4h. Emission rate and drain rate are
different signals and a single "is the builder alive" check conflates them.

WHAT THIS MEASURES (per lane, per run)
--------------------------------------
    depth            open PRs in the lane right now
    opened_24h       emission rate
    merged_24h       drain rate
    validity         fraction of open PRs whose diff passes the lane's validator
    silent_for       hours since the lane last emitted
    undrained_for    hours since the lane last merged

ALARMS ARE ON THE DERIVATIVE, NOT THE LEVEL
-------------------------------------------
`36 open` is not a defect; `36 open and 0 valid` is. `no merge for 10h` is not a
defect if nothing was emitted either. Every alarm below compares two facts, because
a threshold on one number is how you get an instrument that cries wolf until nobody
reads it -- the failure mode of every monitor in this repo's history.

USAGE
    python tools/queue_census.py                 # human table, exit 1 on alarm
    python tools/queue_census.py --json          # machine-readable snapshot
    python tools/queue_census.py --no-validate   # skip diff fetches (fast)
    python tools/queue_census.py --quiet         # verdict line only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("ZO_SENTINEL_REPO", "rob531/zo-sentinel")
HISTORY_DIR = os.path.join(ROOT, "artifacts", "queue_census")

sys.path.insert(0, os.path.join(ROOT, "tools"))

# --------------------------------------------------------------------------
# Lanes
# --------------------------------------------------------------------------
# A lane is an EMITTER, not a topic. The point is to attribute a collapse to the
# thing that produced it, so `validator` names the check that the lane's own
# output must pass. A lane with validator=None is still counted -- depth and rates
# need no validator -- it simply cannot raise VALIDITY_COLLAPSE.
LANES = [
    {
        "name": "builder:manifest",
        "label": "autonomous-build",
        "path_re": r"^services/[^/]+/[^/]+/service\.toml$",
        "single_file": True,
        "validator": "service_manifest",
    },
    {
        "name": "builder:router",
        "label": "autonomous-build",
        "path_re": r"^services/[^/]+/[^/]+/router\.py$",
        "single_file": True,
        "validator": "python_syntax",
    },
    {
        "name": "builder:logic",
        "label": "autonomous-build",
        "path_re": r"^services/[^/]+/[^/]+/logic\.py$",
        "single_file": True,
        "validator": "python_syntax",
    },
    {
        "name": "builder:contract",
        "label": "autonomous-build",
        "path_re": r"^services/[^/]+/[^/]+/contract\.py$",
        "single_file": True,
        "validator": "python_syntax",
    },
    {
        "name": "builder:other",
        "label": "autonomous-build",
        "path_re": None,
        "single_file": False,
        "validator": None,
    },
    {
        "name": "human/fu",
        "label": None,
        "path_re": None,
        "single_file": False,
        "validator": None,
    },
]

# --------------------------------------------------------------------------
# Alarm thresholds -- every one of them compares TWO facts.
# --------------------------------------------------------------------------
MIN_COHORT = 5          # below this a validity rate is noise, not a signal
VALIDITY_FLOOR = 0.34   # a lane emitting mostly-invalid output has regressed
SILENT_HOURS = 6.0      # a lane that normally emits and has stopped
UNDRAINED_HOURS = 8.0   # open work with no merge while emission continues
DIVERGENCE_RATIO = 2.0  # opened_24h >= 2x merged_24h with real depth

# Declare hatch. Mirrors tools/reachability_deferred.json: an entry must carry a
# REASON and an EXPIRES date. A declaration that is stale or reasonless does NOT
# suppress -- it is reported as a defect in its own right, because "the gate is
# green because someone silenced it in June" is the failure this whole file exists
# to make impossible.
DECLARED_PATH = os.path.join(ROOT, "tools", "queue_census_declared.json")


def _gh_json(*args) -> list:
    r = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("gh failed: %s" % (r.stderr or "")[:400])
    return json.loads(r.stdout or "[]")


def _parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def lane_of(pr: dict) -> str:
    labels = {lb["name"] for lb in pr.get("labels") or []}
    paths = [f.get("path", "") for f in pr.get("files") or []]
    for lane in LANES:
        if lane["label"] and lane["label"] not in labels:
            continue
        if lane["single_file"] and len(paths) != 1:
            continue
        if lane["path_re"] and not (paths and re.match(lane["path_re"], paths[0])):
            continue
        return lane["name"]
    return "human/fu"


# --------------------------------------------------------------------------
# Validators -- each answers "would this diff be accepted by its consumer?"
# --------------------------------------------------------------------------
def _added_lines(number: int) -> str:
    r = subprocess.run(["gh", "pr", "diff", str(number), "-R", REPO],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        return ""
    return "\n".join(ln[1:] for ln in (r.stdout or "").splitlines()
                     if ln.startswith("+") and not ln.startswith("+++"))


def _v_service_manifest(number: int, paths: list) -> tuple[bool, str]:
    import check_service_manifests as C
    added = _added_lines(number)
    if not added.strip():
        return False, "empty or unreadable diff"
    svc = paths[0].split("/")[2] if paths and len(paths[0].split("/")) > 2 else ""
    verdict, detail = C.classify_source(added, svc)
    return verdict == "OK", "" if verdict == "OK" else "%s: %s" % (verdict, detail)


def _v_python_syntax(number: int, paths: list) -> tuple[bool, str]:
    added = _added_lines(number)
    if not added.strip():
        return False, "empty or unreadable diff"
    try:
        compile(added, paths[0] if paths else "<diff>", "exec")
    except SyntaxError as exc:
        return False, "SyntaxError line %s: %s" % (exc.lineno, exc.msg)
    # A file that compiles but is a 61-byte stub is the hollow-scaffold defect.
    if len(added.strip().splitlines()) < 3:
        return False, "hollow: %d added line(s)" % len(added.strip().splitlines())
    return True, ""


VALIDATORS = {
    "service_manifest": _v_service_manifest,
    "python_syntax": _v_python_syntax,
}


# --------------------------------------------------------------------------
def collect(validate: bool = True, merged_sample: int = 200) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    open_prs = _gh_json("pr", "list", "-R", REPO, "--state", "open", "--limit", "300",
                        "--json", "number,title,createdAt,labels,files")
    merged = _gh_json("pr", "list", "-R", REPO, "--state", "merged", "--limit",
                      str(merged_sample), "--json",
                      "number,title,mergedAt,labels,files")

    lanes: dict[str, dict] = {
        lane["name"]: {
            "name": lane["name"], "validator": lane["validator"],
            "depth": 0, "opened_24h": 0, "merged_24h": 0,
            "valid": 0, "checked": 0, "invalid_examples": [],
            "last_open": None, "last_merge": None,
        } for lane in LANES
    }

    for pr in open_prs:
        ln = lanes[lane_of(pr)]
        ln["depth"] += 1
        created = _parse(pr["createdAt"])
        if created:
            age = (now - created).total_seconds() / 3600.0
            if age <= 24:
                ln["opened_24h"] += 1
            if ln["last_open"] is None or created > ln["last_open"]:
                ln["last_open"] = created

    for pr in merged:
        ln = lanes[lane_of(pr)]
        m = _parse(pr.get("mergedAt"))
        if not m:
            continue
        if (now - m).total_seconds() / 3600.0 <= 24:
            ln["merged_24h"] += 1
        if ln["last_merge"] is None or m > ln["last_merge"]:
            ln["last_merge"] = m

    if validate:
        for pr in open_prs:
            name = lane_of(pr)
            ln = lanes[name]
            fn = VALIDATORS.get(ln["validator"] or "")
            if not fn:
                continue
            paths = [f.get("path", "") for f in pr.get("files") or []]
            ok, why = fn(pr["number"], paths)
            ln["checked"] += 1
            if ok:
                ln["valid"] += 1
            elif len(ln["invalid_examples"]) < 3:
                ln["invalid_examples"].append({"pr": pr["number"], "why": why})

    out = []
    for ln in lanes.values():
        lo, lm = ln.pop("last_open"), ln.pop("last_merge")
        ln["silent_for"] = round((now - lo).total_seconds() / 3600.0, 1) if lo else None
        ln["undrained_for"] = round((now - lm).total_seconds() / 3600.0, 1) if lm else None
        ln["validity"] = round(ln["valid"] / ln["checked"], 3) if ln["checked"] else None
        out.append(ln)

    return {"at": now.isoformat(), "repo": REPO, "validated": validate,
            "open_total": len(open_prs), "lanes": out}


# --------------------------------------------------------------------------
def _head_sha() -> str:
    """The sha the decision was made ON. A halt with no basis cannot be checked for
    staleness later -- the defect that let a 13:49Z document certify a 10:49Z sha."""
    try:
        r = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        return (r.stdout or "").strip()[:12]
    except OSError:
        return ""


def previous() -> dict | None:
    p = os.path.join(HISTORY_DIR, "latest.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def alarms(snap: dict, prev: dict | None) -> list[dict]:
    prev_lanes = {l["name"]: l for l in (prev or {}).get("lanes", [])}
    out = []
    for ln in snap["lanes"]:
        n, was = ln["name"], prev_lanes.get(ln["name"], {})

        # 1. The 2026-07-29 defect: a lane emitting output its own consumer rejects.
        if ln["checked"] >= MIN_COHORT and ln["validity"] is not None \
                and ln["validity"] < VALIDITY_FLOOR:
            out.append({
                "lane": n, "kind": "VALIDITY_COLLAPSE",
                "detail": "%d/%d open PRs valid (%.0f%%); the emitter is producing "
                          "work its consumer refuses"
                          % (ln["valid"], ln["checked"], 100 * ln["validity"]),
                "examples": ln["invalid_examples"],
                "action": "halt this lane and fix the emitter -- draining will not help",
            })

        # 2. The 2026-07-30 defect: emission continues, nothing merges.
        if ln["depth"] > 0 and (ln["undrained_for"] or 0) >= UNDRAINED_HOURS \
                and ln["opened_24h"] > 0:
            out.append({
                "lane": n, "kind": "UNDRAINED",
                "detail": "%d open, still emitting (%d in 24h), but nothing merged "
                          "for %.1fh" % (ln["depth"], ln["opened_24h"], ln["undrained_for"]),
                "action": "the blockage is downstream of the emitter -- check gates/review",
            })

        # 3. A lane that used to emit and stopped. Requires PRIOR evidence of
        #    emission, so a lane that never runs cannot alarm forever.
        if (ln["silent_for"] or 0) >= SILENT_HOURS and was.get("opened_24h", 0) > 0 \
                and ln["opened_24h"] == 0:
            out.append({
                "lane": n, "kind": "LANE_SILENT",
                "detail": "no emission for %.1fh (was emitting %d/24h at last census)"
                          % (ln["silent_for"], was["opened_24h"]),
                "action": "check the emitter is alive and its input queue is non-empty",
            })

        # 4. Depth rising faster than it drains. Requires a REAL prior depth:
        #    `was.get("depth", 0)` made every lane look like growth-from-zero on the
        #    first census, fabricating a trend out of a missing baseline. Same defect
        #    the ratchet's trend log avoids by writing empty deltas on its first row.
        if "depth" in was and ln["depth"] >= MIN_COHORT \
                and ln["opened_24h"] >= DIVERGENCE_RATIO * max(ln["merged_24h"], 1) \
                and ln["depth"] > was["depth"]:
            out.append({
                "lane": n, "kind": "DIVERGING",
                "detail": "opened %d vs merged %d in 24h; depth %d -> %d"
                          % (ln["opened_24h"], ln["merged_24h"],
                             was.get("depth", 0), ln["depth"]),
                "action": "emission outruns drain; cap the lane or widen the gate",
            })
    return out


def load_declared(path: str | None = None, now: dt.datetime | None = None) -> dict:
    """Return {active, stale, reasonless} declarations.

    Accepts the hand-written shapes a human will actually produce:
        [{"lane": "...", "kind": "UNDRAINED", "reason": "...", "expires": "2026-08-06"}]
    A missing file is not an error -- the hatch ships EMPTY on purpose.
    """
    path = path or DECLARED_PATH
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"active": [], "stale": [], "reasonless": []}
    if not os.path.isfile(path):
        return out
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for e in raw if isinstance(raw, list) else raw.get("declared", []):
        if not isinstance(e, dict) or not e.get("lane") or not e.get("kind"):
            continue
        if not str(e.get("reason") or "").strip():
            out["reasonless"].append(e)
            continue
        exp = str(e.get("expires") or "").strip()
        try:
            when = dt.datetime.fromisoformat(exp).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            # No parseable expiry is the same defect as no reason: it never lapses.
            out["reasonless"].append(e)
            continue
        (out["stale"] if when < now else out["active"]).append(e)
    return out


def apply_declared(alarm_list: list, declared: dict) -> tuple[list, list]:
    """Split alarms into (live, suppressed). Suppressed ones are still RETURNED so
    the caller prints them -- a hidden alarm is an unread one."""
    keys = {(e["lane"], e["kind"]) for e in declared["active"]}
    live, supp = [], []
    for a in alarm_list:
        if (a["lane"], a["kind"]) in keys:
            match = next(e for e in declared["active"]
                         if (e["lane"], e["kind"]) == (a["lane"], a["kind"]))
            supp.append(dict(a, declared_reason=match["reason"],
                             declared_expires=match["expires"]))
        else:
            live.append(a)
    return live, supp


def emit_halts(alarm_list: list, mode: str, sha: str = "") -> list:
    """Turn VALIDITY_COLLAPSE alarms into halt decisions.

    ONLY that alarm class. UNDRAINED means the blockage is already downstream of the
    emitter -- halting the emitter would be treating a symptom in the wrong organ.
    LANE_SILENT means the lane has already stopped; halting a stopped lane is theatre.
    A lane producing output its own consumer refuses is the one case where stopping
    is the correct act.

    Defaults to SHADOW at every layer. `lane_halt` writes shadow records to a
    directory `is_halted()` never reads, so a shadow decision has no code path to
    an effect -- not a flag that could be misread.
    """
    if mode == "off":
        return []
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import lane_halt

    out = []
    for a in alarm_list:
        if a["kind"] != "VALIDITY_COLLAPSE":
            continue
        out.append(lane_halt.raise_halt(
            lane=a["lane"],
            reason="%s -- %s" % (a["kind"], a["detail"]),
            sha=sha,
            source="queue_census",
            mode=lane_halt.MODE_ARMED if mode == "armed" else lane_halt.MODE_SHADOW,
        ))
    return out


def persist(snap: dict, alarm_list: list) -> str:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    snap = dict(snap, alarms=alarm_list)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    hist = os.path.join(HISTORY_DIR, "census-%s.json" % stamp)
    with open(hist, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(snap, fh, indent=1)
    with open(os.path.join(HISTORY_DIR, "latest.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(snap, fh, indent=1)
    return hist


def render(snap: dict, prev: dict | None, alarm_list: list,
           suppressed: list | None = None, declared: dict | None = None,
           halts: list | None = None) -> str:
    prev_lanes = {l["name"]: l for l in (prev or {}).get("lanes", [])}
    lines = ["", "QUEUE CENSUS  %s  (%s, %d open)"
             % (snap["at"][:16].replace("T", " "), snap["repo"], snap["open_total"]), ""]
    lines.append("  %-18s %6s %8s %8s %9s %8s %10s"
                 % ("lane", "depth", "open/24h", "mrg/24h", "validity", "silent", "undrained"))
    lines.append("  " + "-" * 76)
    for ln in snap["lanes"]:
        was = prev_lanes.get(ln["name"], {})
        d = ln["depth"]
        delta = "" if "depth" not in was else (
            " (%+d)" % (d - was["depth"]) if d != was["depth"] else " ( =)")
        v = "-" if ln["validity"] is None else "%.0f%% %d/%d" % (
            100 * ln["validity"], ln["valid"], ln["checked"])
        lines.append("  %-18s %6s %8d %8d %9s %8s %10s"
                     % (ln["name"], "%d%s" % (d, delta), ln["opened_24h"],
                        ln["merged_24h"], v,
                        "-" if ln["silent_for"] is None else "%.1fh" % ln["silent_for"],
                        "-" if ln["undrained_for"] is None else "%.1fh" % ln["undrained_for"]))
    if prev is None:
        lines += ["", "  (no prior census -- derivative alarms arm on the next run)"]
    lines.append("")
    for e in (declared or {}).get("reasonless", []):
        lines.append("  REASONLESS DECLARATION (ignored): %s/%s -- no reason or no "
                     "parseable expires" % (e.get("lane"), e.get("kind")))
    for e in (declared or {}).get("stale", []):
        lines.append("  EXPIRED DECLARATION (ignored): %s/%s expired %s"
                     % (e.get("lane"), e.get("kind"), e.get("expires")))
    for a in suppressed or []:
        lines.append("  SUPPRESSED [%s] %s -- %s (expires %s)"
                     % (a["kind"], a["lane"], a["declared_reason"],
                        a["declared_expires"]))
    if suppressed:
        lines.append("")
    if alarm_list:
        lines.append("  ALARMS")
        for a in alarm_list:
            lines.append("    [%s] %s" % (a["kind"], a["lane"]))
            lines.append("        %s" % a["detail"])
            lines.append("        -> %s" % a["action"])
            for ex in a.get("examples", []):
                lines.append("        e.g. #%s %s" % (ex["pr"], ex["why"]))
        lines.append("")
    for h in halts or []:
        lines.append("  %s [%s] %s%s"
                     % ("SHADOW HALT (recorded, blocks nothing)" if h.get("shadowed")
                        else "HALT RAISED", h["lane"], h.get("decided_on_sha") or "-",
                        "" if h.get("raised") or h.get("shadowed") else " (already halted)"))
    if halts:
        lines.append("")
    lines.append("verdict: %s  (%d alarm(s) across %d lane(s))"
                 % ("ALARM" if alarm_list else "OK", len(alarm_list), len(snap["lanes"])))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="census the open PR queue by lane")
    ap.add_argument("--json", action="store_true", help="emit the snapshot as JSON")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip per-PR diff fetches (depth/rates only)")
    ap.add_argument("--quiet", action="store_true", help="verdict line + exit code only")
    ap.add_argument("--no-persist", action="store_true", help="do not write history")
    ap.add_argument("--halt-mode", choices=("off", "shadow", "armed"),
                    default="armed",
                    help="what to do with VALIDITY_COLLAPSE. ARMED (default since 2026-07-30) raises a real per-lane halt; shadow only RECORDS what it would have done and cannot block; off disables. Armed on the chairman's ruling AFTER the shadow report showed 0 halts firing today and the 7/29 founding case reproducing.")
    args = ap.parse_args(argv)

    prev = previous()
    snap = collect(validate=not args.no_validate)
    declared = load_declared()
    alarm_list, suppressed = apply_declared(alarms(snap, prev), declared)
    if not args.no_persist:
        persist(snap, alarm_list)
    halts = emit_halts(alarm_list, args.halt_mode, _head_sha())

    if args.json:
        print(json.dumps(dict(snap, alarms=alarm_list,
                              suppressed=suppressed, halts=halts), indent=1))
    elif args.quiet:
        print("verdict: %s (%d alarm)" % ("ALARM" if alarm_list else "OK", len(alarm_list)))
    else:
        print(render(snap, prev, alarm_list, suppressed, declared, halts))
    return 1 if alarm_list else 0


if __name__ == "__main__":
    sys.exit(main())
