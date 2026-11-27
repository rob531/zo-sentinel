#!/usr/bin/env python3
"""row_predicates.py -- every C3/C4 Gap Register row names the check that holds it there.

THE GAP (chairman/QUEUE.md 0a, per-row half; class GC-3 + GC-1)
  gap_claim_gate.py checks that a CLAIM moves the register. Nothing checked that a
  grade, once given, is still held: a C3 row's census test could be deleted, or its
  only evidence could be a tower measurement three months old, and the register still
  read C3. chairman/row_predicates.json maps each C3+ row to its predicate.

RULES (register at head, and at --base-ref when given)
  P1 NO-PREDICATE   a row at C3/C4 has no predicate entry.
  P2 DEAD-TEST      a 'test' predicate path is missing, or is not a file the required
                    pytest job collects (tests/test_*.py).
  P3 ORPHAN         a predicate names a row the register does not have.
  P4 STALE-RAISE    this PR raises a row to C3/C4 while its UNKNOWN-kind predicate
                    ('measured' / 'sibling') is past measured_on + budget_days.
  A 'measured'/'sibling' predicate past budget is STALE: reported, escalated by the
  nightly (--report), and never green-by-default, but it does not block unrelated PRs.

  rc 0 GREEN, 1 RED, 2 UNKNOWN (register or predicate file unreadable).
  python tools/row_predicates.py [--base-ref SHA]      # PR gate
  python tools/row_predicates.py --report [--out F]    # nightly: JSON with `escalate`
  python tools/row_predicates.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bounded  # noqa: E402
import gap_claim_gate as gcg  # noqa: E402

PREDICATES = os.path.join(ROOT, "chairman", "row_predicates.json")
GREEN, RED, UNKNOWN = 0, 1, 2
UNKNOWN_KINDS = ("measured", "sibling")


def collected(path: str) -> bool:
    parts = path.split("/")
    return len(parts) >= 2 and parts[0] == "tests" and parts[-1].startswith("test_") and path.endswith(".py")


def age_state(pred: dict, today: dt.date, default_budget: int) -> dict:
    budget = int(pred.get("budget_days", default_budget))
    try:
        on = dt.date.fromisoformat(pred.get("measured_on", ""))
    except ValueError:
        return {"age_days": None, "budget_days": budget, "stale": True}  # undated is not fresh
    age = (today - on).days
    return {"age_days": age, "budget_days": budget, "stale": age > budget}


def evaluate(head: str, preds: dict, today: dt.date, base: str | None = None,
             exists=lambda p: os.path.isfile(os.path.join(ROOT, p))) -> tuple[list[str], list[dict]]:
    h = gcg.rows(head)
    b = gcg.rows(base) if base is not None else {}
    table = preds.get("rows", {})
    default_budget = int(preds.get("default_budget_days", 30))
    bad, report = [], []
    for gid, (grade, _line) in sorted(h.items()):
        if grade is None or grade < 3:
            continue
        p = table.get(gid)
        if p is None:
            bad.append(f"P1 {gid}: graded C{grade} but chairman/row_predicates.json names no predicate")
            continue
        kind = p.get("kind")
        entry = {"row": gid, "grade": grade, "kind": kind}
        if kind == "test":
            paths = p.get("paths") or []
            dead = [x for x in paths if not (exists(x) and collected(x))]
            if not paths or dead:
                bad.append(f"P2 {gid}: test predicate {dead or '(no paths)'} missing or not pytest-collected")
            entry["state"] = "RED" if (not paths or dead) else "ARMED"
        elif kind in UNKNOWN_KINDS:
            entry.update(age_state(p, today, default_budget))
            entry["state"] = "STALE" if entry["stale"] else "UNKNOWN-IN-BUDGET"
            bg = b.get(gid, (None, ""))[0]
            raised = base is not None and (bg is None or grade > bg)
            if raised and entry["stale"]:
                bad.append(f"P4 {gid}: raised to C{grade} on {kind} evidence {entry['age_days']} days old "
                           f"(budget {entry['budget_days']}); re-measure and bump measured_on")
        else:
            bad.append(f"P2 {gid}: unknown predicate kind {kind!r}")
            entry["state"] = "RED"
        report.append(entry)
    for gid in sorted(set(table) - set(h)):
        bad.append(f"P3 {gid}: predicate names a row the register does not have")
    return bad, report


def _load():
    with open(os.path.join(ROOT, gcg.REGISTER), encoding="utf-8") as fh:
        head = fh.read()
    with open(PREDICATES, encoding="utf-8") as fh:
        preds = json.load(fh)
    return head, preds


def self_test() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(("pass " if cond else "FAIL ") + name)
        ok = ok and cond

    today = dt.date(2026, 11, 27)
    reg = lambda g7, g9=1: (f"| GR-7 | a | GC-6 | **C{g7}** | C4 | x | d |\n"
                            f"| GR-9 | b | GC-8 | **C{g9}** | C4 | y | d |\n")
    have = {"tests/test_x.py", "tools/x.py"}.__contains__
    t = {"rows": {"GR-7": {"kind": "test", "paths": ["tests/test_x.py"]}}}
    expect("C3 row with a live test predicate is green", evaluate(reg(3), t, today, exists=have)[0] == [])
    expect("C3 row with no predicate is red (P1)",
           [v[:7] for v in evaluate(reg(3), {"rows": {}}, today, exists=have)[0]] == ["P1 GR-7"])
    expect("deleted test is red (P2)", evaluate(reg(3), {"rows": {"GR-7": {"kind": "test", "paths": ["tests/test_gone.py"]}}},
                                                today, exists=have)[0][0].startswith("P2 GR-7"))
    expect("a non-collected path is red (P2)", evaluate(reg(4), {"rows": {"GR-7": {"kind": "test", "paths": ["tools/x.py"]}}},
                                                        today, exists=have)[0][0].startswith("P2 GR-7"))
    expect("a C1 row needs no predicate", evaluate(reg(1), {"rows": {}}, today, exists=have)[0] == [])
    expect("predicate for a missing row is red (P3)",
           any(v.startswith("P3 GR-99") for v in evaluate(reg(1), {"rows": {"GR-99": {"kind": "test", "paths": []}}},
                                                           today, exists=have)[0]))
    old = {"rows": {"GR-7": {"kind": "measured", "measured_on": "2026-08-30"}}}
    bad, rep = evaluate(reg(3), old, today, base=reg(3), exists=have)
    expect("stale measured evidence on an unmoved row is reported, not red", bad == [] and rep[0]["state"] == "STALE")
    bad, _ = evaluate(reg(3), old, today, base=reg(2), exists=have)
    expect("raising a row on stale evidence is red (P4)", bad and bad[0].startswith("P4 GR-7"))
    fresh = {"rows": {"GR-7": {"kind": "sibling", "measured_on": "2026-11-20"}}}
    bad, rep = evaluate(reg(3), fresh, today, base=reg(2), exists=have)
    expect("raising on in-budget sibling evidence is green", bad == [] and rep[0]["state"] == "UNKNOWN-IN-BUDGET")
    undated = {"rows": {"GR-7": {"kind": "measured"}}}
    expect("undated UNKNOWN evidence is stale, never fresh",
           evaluate(reg(3), undated, today, exists=have)[1][0]["state"] == "STALE")
    print("SELF-TEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-ref", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    try:
        head, preds = _load()
        base = gcg._git_show(a.base_ref) if a.base_ref else None
    except (OSError, ValueError, RuntimeError) as e:
        print(f"row_predicates: UNKNOWN ({e})")
        return UNKNOWN
    if not gcg.rows(head):
        print("row_predicates: UNKNOWN (no register rows parsed)")
        return UNKNOWN
    today = dt.datetime.now(dt.timezone.utc).date()
    bad, rep = evaluate(head, preds, today, base)
    stale = [r for r in rep if r["state"] == "STALE"]
    if a.report:
        out = {"generated_on": today.isoformat(), "violations": bad, "rows": rep,
               "stale": [r["row"] for r in stale], "escalate": bool(bad or stale)}
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2)
        print(json.dumps(out, indent=2))
        return GREEN
    shown = bounded.sample(stale, where="the --report JSON",
                           render=lambda r: f"{r['row']} {r['age_days']}d")
    print(f"row_predicates: {'RED' if bad else 'GREEN'} ({len(rep)} C3+ rows; {len(stale)} stale UNKNOWN: {shown})")
    for v in bad:
        print("  " + v)
    return RED if bad else GREEN


if __name__ == "__main__":
    sys.exit(main())
