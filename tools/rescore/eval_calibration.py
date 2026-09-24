#!/usr/bin/env python3
"""eval_calibration.py -- score the -1 remap on the FULL sample, not just the
rows the two raters fought over.

THE HOLE THIS FILLS
-------------------
CALIBRATION.json (calibrate.py, 2026-09-03) fit and scored the ladder shift on
the ADJUDICATED set: the 121 items where zo and the second rater DISAGREED.
Every axis row where they AGREED was never adjudicated and so never entered
either half of that split. That is the majority of rows. On an agreed row a -1
shift can only ever turn a right answer into a wrong one, so the held-out
+22.2pp / +23.5pp headline is an upper bound measured on the subset most
favourable to the shift. It is NOT the corpus-level effect.

This script reconstructs truth for EVERY sampled axis row:

    both raters agreed          -> truth = the agreed label (no adjudication
                                   was needed; the two independent raters
                                   already concur)
    they disagreed              -> truth = the adjudicator's resolution
                                   (choice A/B resolved through
                                   ADJUDICATION_KEY, or the explicit
                                   `correct` label on a NEITHER)
    adjudicator said
    UNDETERMINABLE, or the
    rater marked the axis
    unanswerable, or a label
    is off the ladder          -> EXCLUDED, and counted as excluded

and then reports, per axis, accuracy with no shift versus accuracy with the -1
shift, over all included rows -- agreed and contested together -- plus the same
numbers split by class so the difference between this and CALIBRATION.json is
visible rather than asserted.

The shift applied here is the PRODUCTION one: the clamp and the
"never shift an explicit UNKNOWN" rule are imported from calibration.py, so
this evaluation cannot drift away from what the layer would actually do.

READ THE AGREED COLUMN HONESTLY
-------------------------------
zo is ONE OF THE TWO RATERS. So on an agreed row, truth == zo's own label by
construction, and the no-shift accuracy of the agreed block is 100% by
construction too -- any shift can only lose ground there. That is a real
asymmetry, and it is the reason the full-sample number is structurally less
flattering than the contested-only one, not merely an artefact to wave away:
two independent raters concurring is the best truth proxy available, and a
remap that overturns concurrence is doing damage unless something says
otherwise. The agreed and contested columns are printed SEPARATELY so this is
visible rather than buried in a single blended figure.

INPUTS (all read-only)
    SEALED_KEY.json        zo's labels per sampled item
    resp_gemini.json       the second rater's labels + answerable flags
    ADJUDICATION_KEY.json  contested-row key (zo/rater behind A/B)
    adj_resp*.json         the adjudicator's replies
    CALIBRATION.json       the contested-only result, quoted for contrast

Every input is required. If one is missing or unreadable this script names it
and exits non-zero. It never prints a number it could not compute.

usage:
    python eval_calibration.py [--dir <gemini_corpus_eval dir>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from calibration import LADDERS, NEVER_SHIFT_LABELS, SHIFTS
except ImportError as exc:                                    # pragma: no cover
    print("MISSING INPUT: cannot import calibration.py from {} ({})".format(HERE, exc),
          file=sys.stderr)
    raise SystemExit(2)

DEFAULT_DIR = Path(r"D:\zo\Zocomputer Agents\gemini_corpus_eval")

REQUIRED = ["SEALED_KEY.json", "resp_gemini.json", "ADJUDICATION_KEY.json",
            "CALIBRATION.json"]
ADJ_GLOB = "adj_resp*.json"

# The remap only ever claims these two. The other ordinal axes are reported
# anyway: a reader must be able to see what a -1 would have done there too.
ORDINAL_AXES = ["overall_risk", "capability_breadth", "data_sensitivity",
                "exploit_surface", "network_egress", "auth_strength"]


def load_json(path: Path, what: str):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        die("MISSING INPUT: {} ({}) does not exist".format(path, what))
    except (OSError, ValueError) as exc:
        die("UNREADABLE INPUT: {} ({}): {}".format(path, what, exc))


def die(msg: str) -> "None":
    print(msg, file=sys.stderr)
    print("Refusing to report an accuracy it could not compute.", file=sys.stderr)
    raise SystemExit(1)


def shifted_label(axis: str, label: str, shift: int) -> str:
    """Apply `shift` under the PRODUCTION rules (clamp + never-shift-UNKNOWN)."""
    ladder = LADDERS.get(axis, ())
    if not ladder or label not in ladder:
        return label
    if label in NEVER_SHIFT_LABELS:
        return label
    j = max(0, min(len(ladder) - 1, ladder.index(label) + shift))
    new = ladder[j]
    return label if new in NEVER_SHIFT_LABELS else new


def truth_from_choice(key_axis, choice, correct):
    if choice == "A":
        return key_axis["zo"] if key_axis.get("A_is") == "zo" else key_axis.get("rater")
    if choice == "B":
        return key_axis["zo"] if key_axis.get("B_is") == "zo" else key_axis.get("rater")
    if choice == "NEITHER":
        return correct or None
    return None                       # UNDETERMINABLE is not a label


def build_rows(d: Path):
    """-> (rows, excl) where rows is [(axis, zo_label, truth, klass)]."""
    sealed = load_json(d / "SEALED_KEY.json", "zo labels for the sample")
    gem = load_json(d / "resp_gemini.json", "second rater's labels")
    adjkey = load_json(d / "ADJUDICATION_KEY.json", "contested-row key")

    adj_files = sorted(d.glob(ADJ_GLOB))
    if not adj_files:
        die("MISSING INPUT: no {} found in {} (the adjudicator's replies)"
            .format(ADJ_GLOB, d))

    items = sealed.get("items")
    if not isinstance(items, dict) or not items:
        die("MALFORMED INPUT: SEALED_KEY.json has no 'items' mapping")
    ratings = gem.get("ratings")
    if not isinstance(ratings, list) or not ratings:
        die("MALFORMED INPUT: resp_gemini.json has no 'ratings' list")
    key_items = adjkey.get("items")
    if not isinstance(key_items, dict) or not key_items:
        die("MALFORMED INPUT: ADJUDICATION_KEY.json has no 'items' mapping")

    rater = {r.get("item_id"): r for r in ratings if r.get("item_id")}

    adj = {}
    for p in adj_files:
        blob = load_json(p, "adjudicator replies")
        for rec in blob.get("adjudications", []):
            if rec.get("item_id"):
                adj[rec["item_id"]] = rec.get("axes", {})
    if not adj:
        die("MALFORMED INPUT: {} carried zero adjudications"
            .format(", ".join(p.name for p in adj_files)))

    # contested key is keyed D###; join back to the sample via _src_item
    by_src = {}
    for did, k in key_items.items():
        src = k.get("_src_item")
        if src:
            by_src.setdefault(src, []).append((did, k))

    rows, excl = [], {}

    def drop(reason):
        excl[reason] = excl.get(reason, 0) + 1

    for sid, item in sorted(items.items()):
        if item.get("kind") != "real":
            continue                       # anchors are probes, not sample
        zo_labels = item.get("zo_label") or {}
        rec = rater.get(sid)
        if rec is None:
            drop("rater never rated the item")
            continue
        r_labels = rec.get("labels") or {}
        answerable = rec.get("answerable") or {}
        for axis in ORDINAL_AXES:
            zo = zo_labels.get(axis)
            rl = r_labels.get(axis)
            if not zo or not rl:
                drop("label absent for an axis")
                continue
            if answerable.get(axis) is False:
                drop("rater marked the axis unanswerable")
                continue
            if zo == rl:
                rows.append((axis, zo, zo, "agreed"))
                continue
            truth = None
            for did, k in by_src.get(sid, []):
                ka = k.get(axis)
                a = adj.get(did, {}).get(axis)
                if not ka or not a:
                    continue
                truth = truth_from_choice(ka, a.get("choice"), a.get("correct"))
                break
            if not truth:
                drop("contested but not adjudicated / UNDETERMINABLE")
                continue
            rows.append((axis, zo, truth, "contested"))
    if not rows:
        die("COULD NOT BUILD A SINGLE (zo, truth) PAIR from {} -- inputs are "
            "present but do not join.".format(d))
    return rows, excl


def acc(rows, shift):
    if not rows:
        return None
    ok = sum(1 for _a, zo, t, _c in rows if shifted_label(_a, zo, shift) == t)
    return ok / len(rows)


def pct(x):
    return "  n/a " if x is None else "{:6.2f}%".format(100 * x)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=os.environ.get("ZO_CALIB_EVAL_DIR",
                                                    str(DEFAULT_DIR)),
                    help="directory holding the gemini_corpus_eval artifacts")
    a = ap.parse_args()
    d = Path(a.dir)
    if not d.is_dir():
        die("MISSING INPUT: artifact directory {} does not exist (or is not a "
            "directory)".format(d))
    missing = [f for f in REQUIRED if not (d / f).is_file()]
    if missing:
        die("MISSING INPUT: {} not found in {}".format(", ".join(missing), d))

    contested_only = load_json(d / "CALIBRATION.json", "contested-only result")
    rows, excl = build_rows(d)

    print("=" * 78)
    print("FULL-SAMPLE CALIBRATION EVALUATION  (source: {})".format(d))
    print("=" * 78)
    print("rows built: {}  (agreed {}, contested-adjudicated {})".format(
        len(rows),
        sum(1 for r in rows if r[3] == "agreed"),
        sum(1 for r in rows if r[3] == "contested")))
    if excl:
        print("excluded:")
        for k in sorted(excl):
            print("    {:6d}  {}".format(excl[k], k))
    print()
    hdr = ("axis", "n_all", "no_shift", "-1 shift", "delta_pp", "n_agr",
           "agr_no", "agr_-1", "n_con", "con_no", "con_-1")
    print("{:<19}{:>6} {:>8} {:>8} {:>9}  {:>5} {:>7} {:>7}  {:>5} {:>7} {:>7}"
          .format(*hdr))
    print("-" * 78)

    results = {}
    for axis in ORDINAL_AXES:
        all_r = [r for r in rows if r[0] == axis]
        if not all_r:
            continue
        agr = [r for r in all_r if r[3] == "agreed"]
        con = [r for r in all_r if r[3] == "contested"]
        a0, a1 = acc(all_r, 0), acc(all_r, -1)
        results[axis] = {"n": len(all_r), "no_shift": a0, "shift": a1,
                         "delta_pp": 100 * (a1 - a0)}
        print("{:<19}{:>6} {} {} {:>+8.2f}  {:>5} {} {}  {:>5} {} {}".format(
            axis + ("*" if axis in SHIFTS else ""), len(all_r), pct(a0), pct(a1),
            100 * (a1 - a0), len(agr), pct(acc(agr, 0)), pct(acc(agr, -1)),
            len(con), pct(acc(con, 0)), pct(acc(con, -1))))
    print("-" * 78)
    print("* = axis the calibration layer would actually remap (SHIFTS)")
    print("NOTE: zo is one of the two raters, so on an AGREED row truth == zo's")
    print("      own label and agr_no is 100% by construction. A shift can only")
    print("      lose ground there. Read agr_* and con_* separately.")
    print()

    print("CONTRAST -- CALIBRATION.json, contested rows only, held-out half:")
    for axis in sorted(SHIFTS):
        c = (contested_only.get("axes") or {}).get(axis)
        if not c:
            print("    {:<19} (absent from CALIBRATION.json)".format(axis))
            continue
        print("    {:<19} {:.2f}% -> {:.2f}%  ({:+.2f}pp, held-out n={})".format(
            axis, 100 * c.get("held_out_accuracy_no_shift", 0),
            100 * c.get("held_out_accuracy_with_shift", 0),
            c.get("improvement_pp", 0), c.get("n_held", "?")))
    print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    bad = []
    for axis in sorted(SHIFTS):
        r = results.get(axis)
        if r is None:
            bad.append("{}: NO ROWS -- cannot be evaluated".format(axis))
            continue
        if r["delta_pp"] <= 0:
            bad.append("{}: full-sample accuracy {:+.2f}pp ({:.2f}% -> {:.2f}%, "
                       "n={})".format(axis, r["delta_pp"], 100 * r["no_shift"],
                                      100 * r["shift"], r["n"]))
    if bad:
        print("ENABLING IS NOT JUSTIFIED BY THIS EVALUATION.")
        print("On the full sample the -1 shift fails to improve:")
        for b in bad:
            print("    " + b)
        print()
        print("The contested-only gain does not survive the rows the two "
              "raters already agreed on. Keep ZO_CALIBRATION_V2 unset.")
        return 0
    print("On the full sample -- agreed rows included -- the -1 shift improves "
          "every axis in SHIFTS:")
    for axis in sorted(SHIFTS):
        r = results[axis]
        print("    {:<19} {:.2f}% -> {:.2f}%  ({:+.2f}pp, n={})".format(
            axis, 100 * r["no_shift"], 100 * r["shift"], r["delta_pp"], r["n"]))
    print()
    print("This removes the contested-only objection recorded in "
          "calibration.py. Enabling ZO_CALIBRATION_V2 is supported by the "
          "measurement; it remains a decision, not an automatic consequence, "
          "because it moves the public risk_tier on ~296,109 servers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
