#!/usr/bin/env python3
"""Merge later per-row re-scores into an earlier run, and re-emit its report.

    python tools/edit_fidelity/merge_runs.py --base results_A.json \
        --override results_B.json results_C.json --out-dir tools/edit_fidelity/results

Why this exists. On 2026-09-13 a hard-shape A/B was scored with an
``extract_code`` that could not close an INDENTED markdown fence, so 12 of 60
model rows were scored against the model's own analysis prose instead of the
module it had returned in the same response. Fixing the extractor and re-running
only those 12 rows (``run_eval.py --only-tasks``) cost $0.56 instead of $2.68
for the whole A/B -- but the corrected table then lives across three files, and
a table assembled by hand in a README is exactly the kind of number this eval
exists to distrust. This merges them into one artifact that anyone can re-derive.

Rules, all of them refusals rather than guesses:

  * an override row must already exist in the base under the same (arm, task),
    otherwise the merge ABORTS -- a merge may correct a row, never invent one;
  * every row records ``source_run``, so no merged row is anonymous;
  * the base's gate, task set and shape are carried through untouched, and an
    override from a different task set or shape is refused.

stdlib only, like the rest of the harness.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Dict, List

_HERE = pathlib.Path(__file__).resolve()
if __package__ in (None, ""):
    sys.path.insert(0, str(_HERE.parents[2]))

from tools.edit_fidelity import run_eval as E  # noqa: E402

EXIT_OK, EXIT_ERROR = 0, 1


def merge(base: Dict, overrides: List[Dict]) -> Dict:
    """Return a new payload with override rows substituted into ``base``."""
    merged = json.loads(json.dumps(base))          # deep copy, no shared state
    base_name = base.get("ts", "base")
    for arm_rows in merged["rows"].values():
        for row in arm_rows:
            row.setdefault("source_run", base_name)
    applied: Dict[str, int] = {}
    for ov in overrides:
        name = ov.get("ts", "override")
        if ov.get("tasks_json") != base.get("tasks_json"):
            raise ValueError(f"{name}: different --tasks-json than the base; refusing to merge")
        if ov.get("shape", {}).get("shape") != base.get("shape", {}).get("shape"):
            raise ValueError(f"{name}: different task shape than the base; refusing to merge")
        for arm, rows in ov["rows"].items():
            if arm not in merged["rows"]:
                raise ValueError(f"{name}: arm {arm!r} is not in the base run; refusing to invent it")
            index = {r["task"]: i for i, r in enumerate(merged["rows"][arm])}
            for row in rows:
                if row["task"] not in index:
                    raise ValueError(f"{name}: task {row['task']!r} is not in the base run's {arm} rows")
                row = dict(row)
                row["source_run"] = name
                merged["rows"][arm][index[row["task"]]] = row
                applied[arm] = applied.get(arm, 0) + 1
    merged["merged_from"] = {"base": base_name, "overrides": [o.get("ts") for o in overrides],
                             "rows_replaced": applied}
    return recompute(merged)


def recompute(payload: Dict) -> Dict:
    """Re-run every aggregate in ``run_eval`` over the merged rows."""
    payload["aggregate"] = {}
    payload["split_by_pass"] = {}
    for arm, rows in payload["rows"].items():
        payload["aggregate"][arm] = E.aggregate(rows)
        payload["split_by_pass"][arm] = E.split_by_pass(rows)
    if "oracle" in payload["rows"] and "sloppy" in payload["rows"]:
        payload["self_test"] = E.check_poles(payload["rows"]["oracle"], payload["rows"]["sloppy"])
    rp, rq = payload["rows"].get("anthropic_preserve"), payload["rows"].get("anthropic_plain")
    payload.pop("paired", None)
    if rp and rq:
        by_task = {r["task"]: r for r in rq}
        pairs_ok = [(p, by_task[p["task"]]) for p in rp
                    if p["task"] in by_task and p.get("fidelity_lev") is not None
                    and by_task[p["task"]].get("fidelity_lev") is not None]
        paired = {
            "n": len(pairs_ok),
            "fidelity": E.sign_test([p["fidelity_lev"] for p, q in pairs_ok],
                                    [q["fidelity_lev"] for p, q in pairs_ok]),
            "delta_cc": E.sign_test([p.get("delta_cc") or 0 for p, q in pairs_ok],
                                    [q.get("delta_cc") or 0 for p, q in pairs_ok]),
            "pass_preserve": sum(1 for p, q in pairs_ok if p.get("passed")),
            "pass_plain": sum(1 for p, q in pairs_ok if q.get("passed")),
        }
        n_eff = paired["fidelity"]["wins"] + paired["fidelity"]["losses"]
        paired["power"] = {
            "n_non_tied": n_eff,
            "power_at": {str(x): round(E.sign_test_power(n_eff, x), 4) for x in (0.65, 0.75, 0.85)},
            "min_detectable_preference_at_80pct": E.min_detectable_preference(n_eff),
        }
        # pass@1 is a PAIRED binary outcome, so it gets a paired test of its own:
        # McNemar's exact test is the sign test over the discordant tasks.
        only_p = sum(1 for p, q in pairs_ok if p.get("passed") and not q.get("passed"))
        only_q = sum(1 for p, q in pairs_ok if q.get("passed") and not p.get("passed"))
        paired["pass_mcnemar"] = {
            "preserve_only": only_p, "plain_only": only_q,
            "concordant": len(pairs_ok) - only_p - only_q,
            "p_two_sided": E.sign_test([0.0] * only_p + [1.0] * only_q,
                                       [1.0] * only_p + [0.0] * only_q)["p_two_sided"],
        }
        payload["paired"] = paired
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--override", nargs="+", required=True)
    ap.add_argument("--out-dir", default=str(_HERE.parent / "results"))
    ap.add_argument("--label", default="merged")
    args = ap.parse_args(argv)

    base = json.loads(pathlib.Path(args.base).read_text(encoding="utf-8"))
    overrides = [json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in args.override]
    try:
        merged = merge(base, overrides)
    except ValueError as e:
        print(f"refusing to merge: {e}", flush=True)
        return EXIT_ERROR
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + args.label
    merged["ts"] = ts
    rj = out_dir / f"results_{ts}.json"
    rm = out_dir / f"report_{ts}.md"
    rj.write_text(json.dumps(merged, indent=1), encoding="utf-8")
    E.write_report(rm, merged)
    print(f"merged {merged['merged_from']['rows_replaced']} row(s)", flush=True)
    print(f"wrote {rj}\nwrote {rm}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
