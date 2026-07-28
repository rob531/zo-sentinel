#!/usr/bin/env python3
"""Schema lint + idempotent REPAIR for FOLLOWUPS.md.

Why this exists (2026-07-28): the chairman spotted that FU-114 was missing its
`- resolution:` key. It was not alone -- 7 entries were missing it. Nothing in
the system could see that, because the ledger has a convention but no validator:
every task may APPEND an entry, only the daily triage task edits status lines,
and neither step ever checked the skeleton. A convention with no checker decays
silently, and a missing `- resolution:` key is invisible in a way an empty one
is not -- triage greps for the key, so an entry without it can never be closed.

v2 (2026-07-28, same day): the chairman's follow-on point -- fixing FU-114 by
appending an empty key CLOSED NOTHING. Every field in the v1 schema was prose,
so SHAPE was the only thing this linter could ever enforce, and a ledger of 148
prose entries can never be acted on by an agent. v2 adds the fields that make an
entry machine-closeable, and the checks that stop a defect being filed without
one:

    E1  missing `- date:`
    E2  missing `- detail:`
    E3  missing `- resolution:`                      <-- auto-fixable
    E4  duplicate FU number
    E5  missing `- class:` / invalid class            <-- auto-fixable (infers)
    E6  class:defect missing `- verify:`              <-- auto-fixable to NONE
                                                          at P2/P3 ONLY
    E7  open P0/P1 defect with `verify: NONE`         <-- NOT fixable, by design
    E8  `- verify:` present but no `- verify_seen_red:` key
    E9  verify command contains a forbidden (mutating/paid) token

E7 is the articulation bar. If a P0/P1 defect cannot say what "fixed" executes
as, it is not yet described well enough to be worked, and no amount of prose
substitutes. The linter will not paper over it.

Usage
    python ledger_lint.py                 # report only, exit 1 if errors
    python ledger_lint.py --fix           # repair E3/E5/E6 in place (backs up)
    python ledger_lint.py --json          # machine-readable, for a task to read
    python ledger_lint.py --stats         # ledger health at a glance

Idempotent: --fix converges. Running it twice is a no-op.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fu_ledger  # noqa: E402
import fu_lock  # noqa: E402

DEFAULT_LEDGER = r"D:\zo\Zocomputer Agents\FOLLOWUPS.md"
BACKUP_ROOT = r"D:\zo\Zocomputer Agents\_followup_backups"

REQUIRED = ("date", "detail", "resolution")
HARD_PRIORITIES = ("P0", "P1")

# Heuristics for inferring `class:` on the 148 legacy entries. Deliberately
# conservative -- anything unmatched becomes `defect`, which is the class that
# DEMANDS a verify, so the failure mode of a bad guess is a loud lint error
# rather than a silently exempted entry.
DIRECTIVE_HINTS = re.compile(
    r"\b(steer|roadmap|decide|decision|policy|ruling|goal|plan|strategy|"
    r"competitive|threat|product|pricing|adopt)\b", re.I)
LEARNING_HINTS = re.compile(
    r"\b(learning|lesson|note to self|retro|postmortem|post-mortem|doctrine|"
    r"principle|capture)\b", re.I)


def infer_class(fu) -> str:
    blob = "%s %s" % (fu.title, (fu.vals.get("detail") or "")[:400])
    if LEARNING_HINTS.search(blob):
        return "learning"
    if DIRECTIVE_HINTS.search(fu.title):
        return "directive"
    return "defect"


def analyse(lines):
    entries = fu_ledger.parse(lines)
    errors, seen = [], {}

    for fu in entries:
        def err(code, msg, fixable=False):
            errors.append({"code": code, "fu": fu.id, "line": fu.start + 1,
                           "message": msg, "fixable": fixable})

        for req in REQUIRED:
            if req not in fu.keys:
                err({"date": "E1", "detail": "E2", "resolution": "E3"}[req],
                    "missing `- %s:`" % req, fixable=(req == "resolution"))

        if fu.num in seen:
            err("E4", "duplicate FU number (first seen line %d) -- a repeat is a dated "
                      "`log:` bullet under the existing entry, never a new heading"
                % (seen[fu.num] + 1))
        seen[fu.num] = fu.start

        if not fu.fu_class:
            err("E5", "missing or invalid `- class:` (expected one of %s)"
                % "/".join(fu_ledger.VALID_CLASS), fixable=True)

        cls = fu.fu_class or infer_class(fu)
        if cls == "defect":
            has_verify_key = "verify" in fu.keys
            if not has_verify_key:
                soft = fu.priority not in HARD_PRIORITIES or not fu.is_open()
                err("E6", "class:defect with no `- verify:` -- nothing can ever close this "
                          "entry without a human reading it", fixable=soft)
            elif fu.verify_is_none and fu.priority in HARD_PRIORITIES and fu.is_open():
                err("E7", "open %s defect with `verify: NONE` -- a P0/P1 that cannot state "
                          "its acceptance test as a command is not yet articulated well "
                          "enough to be worked. Write the predicate or drop the priority."
                    % fu.priority)

            if fu.verify_cmd:
                if "verify_seen_red" not in fu.keys:
                    err("E8", "`- verify:` present with no `- verify_seen_red:` -- a predicate "
                              "never observed failing cannot be trusted to close anything",
                        fixable=True)
                unsafe = fu.unsafe_reason()
                if unsafe:
                    err("E9", "unsafe verify: %s" % unsafe)

    return entries, errors


def repair(lines, entries):
    """Apply the fixable classes bottom-up so earlier indices stay valid."""
    fixed = {"E3": [], "E5": [], "E6": [], "E8": []}
    for fu in sorted(entries, key=lambda f: f.start, reverse=True):
        if "resolution" not in fu.keys:
            pos = fu.end
            while pos - 1 > fu.start and lines[pos - 1].strip() in ("", "---"):
                pos -= 1
            lines.insert(pos, "- resolution:")
            fixed["E3"].append(fu.id)

    for fu in sorted(fu_ledger.parse(lines), key=lambda f: f.start, reverse=True):
        if not fu.fu_class:
            fu_ledger.insert_key(lines, fu, "class", infer_class(fu), before="detail")
            fixed["E5"].append(fu.id)

    for fu in sorted(fu_ledger.parse(lines), key=lambda f: f.start, reverse=True):
        cls = fu.fu_class or infer_class(fu)
        if cls != "defect":
            continue
        soft = fu.priority not in HARD_PRIORITIES or not fu.is_open()
        if "verify" not in fu.keys and soft:
            fu_ledger.insert_key(
                lines, fu, "verify",
                "NONE - legacy entry, predicate not yet written", before="log")
            fixed["E6"].append(fu.id)

    for fu in sorted(fu_ledger.parse(lines), key=lambda f: f.start, reverse=True):
        if fu.verify_cmd and "verify_seen_red" not in fu.keys:
            fu_ledger.insert_key(lines, fu, "verify_seen_red", fu_ledger.NEVER_RED,
                                 before="log")
            fixed["E8"].append(fu.id)

    return {k: list(reversed(v)) for k, v in fixed.items() if v}


def stats(entries):
    by = lambda f: (f or "(unset)")
    out = {"entries": len(entries), "by_status": {}, "by_class": {}, "verifiable": 0,
           "verify_none": 0, "no_verify_key": 0, "seen_red": 0, "open_hard_unverifiable": 0}
    for fu in entries:
        out["by_status"][by(fu.status)] = out["by_status"].get(by(fu.status), 0) + 1
        out["by_class"][by(fu.fu_class)] = out["by_class"].get(by(fu.fu_class), 0) + 1
        if fu.verify_cmd:
            out["verifiable"] += 1
            if fu.seen_red:
                out["seen_red"] += 1
        elif "verify" in fu.keys:
            out["verify_none"] += 1
        else:
            out["no_verify_key"] += 1
        if (fu.is_open() and fu.priority in HARD_PRIORITIES
                and (fu.fu_class or "defect") == "defect" and not fu.verify_cmd):
            out["open_hard_unverifiable"] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        print("FATAL: no ledger at %s" % args.ledger, file=sys.stderr)
        return 2

    with open(args.ledger, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    entries, errors = analyse(lines)
    fixed = {}

    if args.fix and any(e["fixable"] for e in errors):
        stamp = datetime.now(timezone.utc)
        bdir = os.path.join(BACKUP_ROOT, stamp.strftime("%Y-%m-%d"), "ledger-lint")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(args.ledger, os.path.join(
            bdir, "FOLLOWUPS.md.%s.bak" % stamp.strftime("%H%M%SZ")))
        # Re-read UNDER THE LOCK. The analysis above was unlocked, so the file
        # may have moved; the txn re-reads and refuses to commit over another
        # writer's edit rather than clobbering it.
        try:
            with fu_lock.ledger_txn(args.ledger) as txn:
                entries_l, _ = analyse(txn.lines)
                fixed = repair(txn.lines, entries_l)
                lines = txn.lines
        except fu_lock.LedgerChanged as exc:
            print("ABORTED: %s" % exc, file=sys.stderr)
            return 3
        except fu_lock.LedgerBusy as exc:
            print("ABORTED: %s" % exc, file=sys.stderr)
            return 4
        entries, errors = analyse(lines)     # re-analyse to prove convergence

    result = {"ledger": args.ledger, "entries": len(entries), "errors": errors,
              "fixed": fixed, "clean": not errors, "stats": stats(entries)}

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.stats:
        s = result["stats"]
        print("ledger: %s" % args.ledger)
        print("entries: %d" % s["entries"])
        print("  by status : %s" % ", ".join("%s=%d" % kv for kv in sorted(s["by_status"].items())))
        print("  by class  : %s" % ", ".join("%s=%d" % kv for kv in sorted(s["by_class"].items())))
        print("  runnable verify        : %d" % s["verifiable"])
        print("  of those, seen RED     : %d   <- only these can auto-close" % s["seen_red"])
        print("  verify: NONE           : %d" % s["verify_none"])
        print("  no verify key at all   : %d" % s["no_verify_key"])
        print("  OPEN P0/P1 unverifiable: %d   <- the articulation debt" % s["open_hard_unverifiable"])
    else:
        print("ledger: %s" % args.ledger)
        print("FU entries: %d" % len(entries))
        for code, ids in (fixed or {}).items():
            print("REPAIRED %s (%d): %s" % (code, len(ids), ", ".join(ids[:12]) +
                                            (" ..." if len(ids) > 12 else "")))
        if errors:
            print("ERRORS: %d" % len(errors))
            for e in errors:
                print("  %s %-8s line %-6d %s" % (e["code"], e["fu"], e["line"], e["message"]))
        else:
            print("CLEAN")

    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
