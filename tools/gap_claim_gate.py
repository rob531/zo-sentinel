#!/usr/bin/env python3
"""gap_claim_gate.py -- a PR that claims a Gap Register fix must move the register.

THE GAP (chairman/QUEUE.md 0a, "the doctrine must be un-ignorable")
  LOCO_CHAIRMAN.md 3 says only C4 may be called closed and every "fixed" claim states
  its grade, but nothing checked it: a PR could say "closes GR-7" with the register
  row untouched at C1 and every gate stayed green. This makes the claim checkable from
  the PR text + the register diff + repo state alone.

RULES (PR title+body against the register at base and at head)
  R1 CLOSED-BELOW-C4  "close/closes/closed GR-n" while GR-n's head grade is not C4.
  R2 CLAIM-NO-MOVE    "fix/fixes/fixed/resolve(s/d) GR-n" (or a close) while GR-n's
                      grade cell is unchanged from base.
  R3 NO-EVIDENCE      a row whose grade RISES to C3 or C4 cites no backticked repo path
                      that exists at head, nor a GitHub PR link (evidence in a sibling repo).
  R4 UNKNOWN-ROW      a claimed GR-n that has no row at head.
  A grade moving DOWN, or a PR that mentions GR-n without a claim verb, is never red.

  rc 0 GREEN, 1 RED (violations listed), 2 UNKNOWN (register unreadable at base or
  head). CI treats 2 as red: a check that could not read its input has not passed.

  python tools/gap_claim_gate.py --base-ref origin/main [--title T --body-file F]
      (title/body default to env PR_TITLE / PR_BODY)
  python tools/gap_claim_gate.py --self-test
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER = "LOCO_CHAIRMAN.md"
GREEN, RED, UNKNOWN = 0, 1, 2

ROW_RE = re.compile(r"^\|\s*(GR-\d+)\b[^|]*\|")
GRADE_RE = re.compile(r"\*\*C([0-4])\*\*")
PR_LINK_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)(?:::[^`]*)?`")
CLOSE_RE = re.compile(r"\b(?:close[sd]?)\s*:?\s+((?:GR-\d+[\s,/&and]*)+)", re.I)
FIX_RE = re.compile(r"\b(?:fix(?:e[sd])?|resolve[sd]?)\s*:?\s+((?:GR-\d+[\s,/&and]*)+)", re.I)
ID_RE = re.compile(r"GR-\d+")
SCOPED_FIX_RE = re.compile(r"\bfix\((GR-\d+)\)", re.I)  # conventional-commit fix(GR-n):


def rows(text: str) -> dict:
    """GR-id -> (grade int or None, row text). First row wins (the 6.1 table)."""
    out = {}
    for line in (text or "").splitlines():
        m = ROW_RE.match(line)
        if not m or m.group(1) in out:
            continue
        cells = line.split("|")
        cell = cells[4] if len(cells) > 4 else ""
        g = GRADE_RE.search(cell)
        out[m.group(1)] = (int(g.group(1)) if g else None, line)
    return out


def claims(text: str) -> tuple[set, set]:
    closed = {i for m in CLOSE_RE.finditer(text or "") for i in ID_RE.findall(m.group(1))}
    fixed = {i for m in FIX_RE.finditer(text or "") for i in ID_RE.findall(m.group(1))}
    fixed |= set(SCOPED_FIX_RE.findall(text or ""))
    return closed, fixed | closed


def evaluate(pr_text: str, base: str, head: str, exists=lambda p: os.path.exists(os.path.join(ROOT, p))) -> list[str]:
    b, h = rows(base), rows(head)
    closed, claimed = claims(pr_text)
    bad = []
    for gid in sorted(claimed):
        if gid not in h:
            bad.append(f"R4 {gid}: claimed in the PR but has no Gap Register row")
            continue
        hg = h[gid][0]
        if gid in closed and hg != 4:
            bad.append(f"R1 {gid}: PR says closed but the row is C{hg} -- only C4 may be called closed")
        if gid in b and b[gid][0] == hg:
            bad.append(f"R2 {gid}: PR claims a fix but the row's grade did not move (still C{hg})")
    for gid, (hg, line) in sorted(h.items()):
        bg = b.get(gid, (None, ""))[0]
        if hg is not None and hg >= 3 and (bg is None or hg > bg):
            cited = [p for p in PATH_RE.findall(line) if exists(p)] + PR_LINK_RE.findall(line)
            if not cited:
                bad.append(f"R3 {gid}: grade rose to C{hg} but the row cites no evidence "
                           "(an existing repo path, or a merged-PR link for a sibling repo)")
    return bad


def _git_show(ref: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{REGISTER}"], cwd=ROOT,
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git show failed")
    return r.stdout


def run(base_ref: str, pr_text: str) -> tuple[int, list[str]]:
    try:
        base = _git_show(base_ref)
        head = open(os.path.join(ROOT, REGISTER), encoding="utf-8").read()
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return UNKNOWN, [f"UNKNOWN: register unreadable ({e})"]
    if not rows(base) or not rows(head):
        return UNKNOWN, ["UNKNOWN: no Gap Register rows parsed at base or head"]
    bad = evaluate(pr_text, base, head)
    return (RED if bad else GREEN), bad


def self_test() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(("pass " if cond else "FAIL ") + name)
        ok = ok and cond

    def reg(grade, extra=""):
        return f"| GR-7 | drift | GC-6 | **C{grade}** {extra} | C4 | x | d |\n| GR-9 | lanes | GC-8 | **C0** | C4 | y | d |\n"

    have = {"tests/test_x.py"}.__contains__
    expect("closed at C1 is red (R1+R2)",
           any(v.startswith("R1 GR-7") for v in evaluate("Closes GR-7", reg(1), reg(1), have)))
    expect("fix claim with no grade move is red (R2)",
           evaluate("fixes GR-7", reg(1), reg(1), have) == ["R2 GR-7: PR claims a fix but the row's grade did not move (still C1)"])
    expect("genuine move with evidence is green",
           evaluate("fixes GR-7", reg(1), reg(3, "`tests/test_x.py`"), have) == [])
    expect("closed at C4 with evidence is green",
           evaluate("closes GR-7", reg(2), reg(4, "`tests/test_x.py::t`"), have) == [])
    expect("rise to C3 citing a missing path is red (R3)",
           any(v.startswith("R3 GR-7") for v in evaluate("", reg(1), reg(3, "`tests/nope.py`"), have)))
    expect("a sibling-repo PR link counts as evidence",
           evaluate("", reg(1), reg(3, "[x#1](https://github.com/o/zo-fleet-tools/pull/683)"), have) == [])
    expect("a grade moving down is green", evaluate("", reg(3, "`tests/test_x.py`"), reg(1), have) == [])
    expect("mentioning a row without a claim verb is green", evaluate("GR-7: sweep (C1 -> C1)", reg(1), reg(1), have) == [])
    expect("claim of an unknown row is red (R4)",
           any(v.startswith("R4 GR-99") for v in evaluate("fixes GR-99", reg(1), reg(1), have)))
    expect("conventional fix(GR-n) is a claim", "GR-7" in claims("fix(GR-7): sweep")[1])
    expect("lists are all claimed", claims("closes GR-7, GR-9 and GR-12")[0] == {"GR-7", "GR-9", "GR-12"})
    print("SELF-TEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--title", default=None)
    ap.add_argument("--body-file", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    title = a.title if a.title is not None else os.environ.get("PR_TITLE", "")
    body = open(a.body_file, encoding="utf-8").read() if a.body_file else os.environ.get("PR_BODY", "")
    rc, bad = run(a.base_ref, f"{title}\n{body}")
    print(f"gap_claim_gate: {['GREEN', 'RED', 'UNKNOWN'][rc]}")
    for v in bad:
        print("  " + v)
    return rc


if __name__ == "__main__":
    sys.exit(main())
