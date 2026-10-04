#!/usr/bin/env python3
"""stranded_land.py -- land the CLEAN stranded re-emissions (#4079) in rate-limited,
reviewable batches, instead of leaving "land them or discard them" as a sentence.

    python3 tools/stranded_land.py --self-test            # negative controls, spends nothing
    python3 tools/stranded_land.py                        # PLAN only (default): the batch it would land
    python3 tools/stranded_land.py --apply                # branch + commit + push + open ONE PR
    python3 tools/stranded_land.py --limit 3 --apply       # smaller batch; --limit can LOWER, never raise

    rc 0  nothing to do, or the batch landed (PR url printed)
    rc 1  there is work and this was a plan, or the landing refused itself
    rc 2  COULD NOT EVALUATE -- never a pass (R6: unknown is not zero)

WHY THIS EXISTS
    #4079 asked for 69 withdrawn modules to be re-emitted. cycle-0166 (2026-10-01)
    established the re-emission ALREADY HAPPENED: 46 of the 69 are regenerated and
    sitting UNTRACKED on /home/workspace/zo_sentinel -- present on a disk, in no
    repository. cycle-0175 (2026-10-03) graded them: 38 CLEAN, 8 PHANTOM, 0 BROKEN.

    What remained was a sentence -- "review and land the 46 in reviewed batches, or
    rule the stranded set discarded" -- and this issue has already been paid for
    five times by cycles that read a sentence where a tool should have been. A
    sentence is not a batch policy, exactly as the original manifest's "69 PRs at
    once" was not a rate limiter until #4625 made it one. This is that tool for the
    landing half.

    It lands ONLY what the repo's own grader calls CLEAN. It never grades anything
    itself, never deletes, never merges, never force-pushes, and never emits a new
    module.

THE HAZARD IT IS BUILT AROUND, AND THE CONTROL FOR IT
    /home/workspace/zo_sentinel carries ~18,500 untracked files and ~47 dirty
    tracked ones. `git add -A` there is not a commit, it is an incident. So:

      * nothing is ever committed from the runtime tree. A FRESH clone of
        origin/main is made and the candidate bytes are copied into it (FU-067:
        a dirty runtime clone is never used).
      * files are staged by EXPLICIT pathspec, one `git add -- <path>` per
        candidate, and the index is then asserted to equal the intended set
        exactly. A decoy file dropped beside an intended one must NOT appear.
      * self-test pole P5 observes `git add -A` picking the decoy up (RED) and
        this tool's stager refusing it (GREEN). An assertion never seen red is
        not evidence (R4).

BASIS IS PRINTED, NOT IMPLIED (R5): the grader's ref and sha, the clone's base
commit, the runtime tree it copied from, and the counts with their window.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_SLUG = "rob531/zo-sentinel"

# --limit may LOWER these. Nothing in this file may raise them, and --limit is
# clamped rather than honoured when it tries (self-test pole P2).
MAX_PER_RUN = 8
MAX_IN_FLIGHT = 2

BAD_VERDICTS = ("PHANTOM", "BROKEN", "UNKNOWN")
BRANCH_PREFIX = "loop/stranded-land-"
PR_MARKER = "[stranded-land]"   # how in-flight PRs from this tool are counted


# --------------------------------------------------------------------------- sh

def sh(args, cwd=None, check=True, timeout=600):
    r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if check and r.returncode:
        raise RuntimeError("%s -> rc=%s\n%s" % (" ".join(map(str, args)), r.returncode, out[-1200:]))
    return r.returncode, out


# ------------------------------------------------------------------- the grader

def grade(repo: Path, grader: Path, manifest: str | None):
    """Run the repo's OWN grader and return its rows. Two instruments that
    disagree about what CLEAN means would be worse than none, so this shells to
    tools/stranded_review.py rather than re-deciding anything."""
    if not grader.is_file():
        return None, "grader not resolvable at %s" % grader
    out_json = Path(tempfile.mkdtemp(prefix="stranded_land_")) / "grade.json"
    argv = [sys.executable, str(grader), "--repo", str(repo), "--json", str(out_json)]
    if manifest:
        argv += ["--manifest", manifest]
    try:
        sh(argv, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "grader did not run: %s: %s" % (type(exc).__name__, exc)
    if not out_json.is_file():
        return None, "grader wrote no json"
    try:
        return json.loads(out_json.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, "grader json unreadable: %s" % exc


def partition(rows):
    """Split the graded rows into the four buckets this tool acts on.

    ABSENT is kept as its own bucket and never folded into any other count:
    an absent candidate is UNKNOWN territory, not a landed one (R6)."""
    landed, eligible, refused, absent = [], [], [], []
    for r in rows:
        state, verdict = r.get("landed_state"), r.get("verdict")
        if state == "landed":
            landed.append(r)
        elif state == "untracked" and verdict == "CLEAN":
            eligible.append(r)
        elif state == "untracked":
            refused.append(r)
        else:
            absent.append(r)
    eligible.sort(key=lambda r: (r.get("bytes") or 0, r["candidate"]))
    return landed, eligible, refused, absent


# ------------------------------------------------------------------ the stager

def stage_exact(clone: Path, rels):
    """Stage EXACTLY these paths and prove it.

    Returns (staged_sorted, rc). rc != 0 means the index did not end up equal to
    the intended set, and the caller must not commit. This is the function pole
    P5 observes going red against `git add -A`."""
    for rel in rels:
        sh(["git", "add", "--", rel], cwd=clone)
    _, out = sh(["git", "diff", "--cached", "--name-only"], cwd=clone)
    staged = sorted(p for p in out.splitlines() if p.strip())
    want = sorted(rels)
    if staged != want:
        return staged, 1
    return staged, 0


def copy_in(runtime: Path, clone: Path, rels):
    """Copy candidate bytes from the runtime tree into the clean clone.
    Returns the rels that were actually copied; a missing source is reported,
    never counted as copied."""
    done, missing = [], []
    for rel in rels:
        src = runtime / rel
        if not src.is_file():
            missing.append(rel)
            continue
        dst = clone / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        done.append(rel)
    return done, missing


# --------------------------------------------------------------------- in flight

def in_flight(slug: str) -> tuple[int, list, str | None]:
    try:
        _, out = sh(["gh", "pr", "list", "-R", slug, "--state", "open",
                     "--json", "number,title", "--limit", "100"], check=False)
        prs = [p for p in json.loads(out) if PR_MARKER in (p.get("title") or "")]
        return len(prs), prs, None
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        return -1, [], "%s: %s" % (type(exc).__name__, exc)


# -------------------------------------------------------------------- self test

def self_test() -> int:
    """Six poles. Every one must be OBSERVED, including the reds."""
    poles, fails = [], 0

    def pole(name, ok, detail=""):
        nonlocal fails
        poles.append((name, bool(ok), detail))
        if not ok:
            fails += 1

    # P1 -- a bad verdict is never eligible, and is reported as REFUSED.
    rows = [
        {"candidate": "a.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 10},
        {"candidate": "b.py", "landed_state": "untracked", "verdict": "PHANTOM", "bytes": 10},
        {"candidate": "c.py", "landed_state": "untracked", "verdict": "BROKEN", "bytes": 10},
        {"candidate": "d.py", "landed_state": "untracked", "verdict": "UNKNOWN", "bytes": 10},
        {"candidate": "e.py", "landed_state": "landed", "verdict": "CLEAN", "bytes": 10},
        {"candidate": "f.py", "landed_state": "absent", "verdict": None, "bytes": 0},
    ]
    landed, eligible, refused, absent = partition(rows)
    pole("P1 bad verdicts refused, never eligible",
         [r["candidate"] for r in eligible] == ["a.py"] and len(refused) == 3,
         "eligible=%s refused=%s" % ([r["candidate"] for r in eligible],
                                     [r["candidate"] for r in refused]))

    # P3 -- a landed row is skipped: idempotence resolved against state, not a ledger.
    pole("P3 already-landed row skipped",
         [r["candidate"] for r in landed] == ["e.py"] and
         "e.py" not in [r["candidate"] for r in eligible],
         "landed=%s" % [r["candidate"] for r in landed])

    # P4 -- absent is its own bucket. Never summed into landed or eligible.
    pole("P4 absent is not zero and not landed",
         [r["candidate"] for r in absent] == ["f.py"] and
         "f.py" not in [r["candidate"] for r in eligible] and
         "f.py" not in [r["candidate"] for r in landed],
         "absent=%s" % [r["candidate"] for r in absent])

    # P2 -- --limit clamps upward attempts instead of honouring them.
    pole("P2 --limit cannot raise MAX_PER_RUN",
         clamp(99) == MAX_PER_RUN and clamp(3) == 3 and clamp(0) == 0,
         "clamp(99)=%d clamp(3)=%d" % (clamp(99), clamp(3)))

    # P5 -- THE negative control. A decoy beside an intended file must not be
    # staged. `git add -A` is observed picking it up (RED) first, so the pass
    # below is a measured difference and not an untested branch.
    tmp = Path(tempfile.mkdtemp(prefix="stranded_land_p5_"))
    try:
        sh(["git", "init", "-q", str(tmp)])
        sh(["git", "config", "user.email", "t@t"], cwd=tmp)
        sh(["git", "config", "user.name", "t"], cwd=tmp)
        (tmp / "keep.txt").write_text("seed\n", encoding="utf-8")
        sh(["git", "add", "keep.txt"], cwd=tmp)
        sh(["git", "commit", "-qm", "seed"], cwd=tmp)
        d = tmp / "services" / "staged" / "x"
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("intended\n", encoding="utf-8")
        (d / "DECOY_do_not_land.py").write_text("decoy\n", encoding="utf-8")
        intended = ["services/staged/x/__init__.py"]

        # RED pole, observed on purpose: the naive form stages the decoy too.
        sh(["git", "add", "-A"], cwd=tmp)
        _, out = sh(["git", "diff", "--cached", "--name-only"], cwd=tmp)
        naive = sorted(p for p in out.splitlines() if p.strip())
        sh(["git", "reset", "-q"], cwd=tmp)
        naive_caught_decoy = any("DECOY" in p for p in naive)

        staged, rc = stage_exact(tmp, intended)
        pole("P5 explicit stager excludes a decoy that `git add -A` catches",
             naive_caught_decoy and rc == 0 and staged == intended,
             "add -A staged %s ; stage_exact staged %s rc=%d" % (naive, staged, rc))

        # P5b -- the index assertion itself must be able to fail.
        sh(["git", "add", "--", "services/staged/x/DECOY_do_not_land.py"], cwd=tmp)
        _, rc_bad = stage_exact(tmp, intended)
        pole("P5b index assertion observed RED when the index is wrong",
             rc_bad == 1, "rc=%d (1 expected)" % rc_bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # P6 -- no grader => CANNOT EVALUATE, never a pass.
    g, err = grade(Path("."), Path(tempfile.gettempdir()) / "definitely_no_grader_here.py", None)
    pole("P6 missing grader is rc=2 CANNOT EVALUATE, not 0",
         g is None and err is not None, "err=%s" % err)

    for name, ok, detail in poles:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", name,
                               "" if ok else "   <-- " + detail))
    print("SELF-TEST: %d pole(s), %d failing" % (len(poles), fails))
    return 1 if fails else 0


def clamp(n: int) -> int:
    """--limit may lower the batch, never raise it."""
    if n is None:
        return MAX_PER_RUN
    return max(0, min(int(n), MAX_PER_RUN))


# ------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="/home/workspace/zo_sentinel",
                    help="the RUNTIME tree the stranded files actually live in")
    ap.add_argument("--grader", default=None,
                    help="path to tools/stranded_review.py (default: beside this file)")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="lower the batch size; cannot raise MAX_PER_RUN=%d" % MAX_PER_RUN)
    ap.add_argument("--apply", action="store_true", help="actually branch, commit, push, open the PR")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    runtime = Path(a.repo)
    grader = Path(a.grader) if a.grader else Path(__file__).resolve().parent / "stranded_review.py"

    graded, err = grade(runtime, grader, a.manifest)
    if graded is None:
        print("CANNOT EVALUATE: %s" % err)
        print("R6: unknown is not zero. rc=2, never a pass.")
        return 2

    basis = graded.get("basis") or {}
    rows = graded.get("rows") or []
    if not rows:
        print("CANNOT EVALUATE: the grader returned no rows at all.")
        return 2

    landed, eligible, refused, absent = partition(rows)
    want = clamp(a.limit)
    if a.limit is not None and a.limit > MAX_PER_RUN:
        print("NOTE: --limit %d CLAMPED to MAX_PER_RUN=%d. This flag lowers; it cannot raise."
              % (a.limit, MAX_PER_RUN))

    nf, prs, nf_err = in_flight(REPO_SLUG)

    print("BASIS  runtime=%s head=%s grader=%s" % (runtime, basis.get("repo_head"), grader))
    print("       catalog %s = %s table(s), bus_age %.2fd, graded_at %s"
          % (",".join(basis.get("catalog_planes") or []), basis.get("catalog_tables"),
             basis.get("bus_age_days") or -1.0, basis.get("generated_at")))
    print("")
    print("  candidates            %4d" % len(rows))
    print("  landed (tracked)      %4d   <- already in the repository, skipped (idempotent)" % len(landed))
    print("  ELIGIBLE (untracked+CLEAN) %4d" % len(eligible))
    print("  REFUSED (untracked, %s) %4d   <- never landed by this tool"
          % ("/".join(BAD_VERDICTS), len(refused)))
    print("  absent                %4d   <- never regenerated; UNKNOWN, not zero" % len(absent))
    print("  in-flight [stranded-land] PRs %s (ceiling %d)"
          % ("UNREADABLE: %s" % nf_err if nf < 0 else str(nf), MAX_IN_FLIGHT))

    batch = eligible[:want]
    rels = [r["candidate"] for r in batch]
    print("")
    for r in batch:
        print("  batch: %-60s %6dB %4d lines" % (r["candidate"], r.get("bytes") or 0, r.get("lines") or 0))

    if a.json_out:
        Path(a.json_out).write_text(json.dumps({
            "basis": basis, "counts": {"candidates": len(rows), "landed": len(landed),
            "eligible": len(eligible), "refused": len(refused), "absent": len(absent)},
            "refused": [r["candidate"] for r in refused],
            "batch": rels, "in_flight": nf,
        }, indent=2), encoding="utf-8")

    if not eligible:
        print("\nNOTHING ELIGIBLE. rc=0.")
        return 0
    if nf < 0:
        print("\nREFUSED: the in-flight PR count could not be read, and an unknown is not a zero. rc=2.")
        return 2
    if nf >= MAX_IN_FLIGHT:
        print("\nHELD: %d [stranded-land] PR(s) already open (ceiling %d). Land or close those first: %s"
              % (nf, MAX_IN_FLIGHT, ", ".join("#%d" % p["number"] for p in prs)))
        return 1
    if not batch:
        print("\n--limit 0: nothing requested. rc=1 (there IS work).")
        return 1
    if not a.apply:
        print("\nPLAN ONLY (rc=1). Re-run with --apply to open ONE PR for the %d file(s) above." % len(rels))
        return 1

    # ---- apply: a FRESH clone, never the dirty runtime tree (FU-067)
    work = Path(tempfile.mkdtemp(prefix="stranded_land_apply_"))
    clone = work / "repo"
    try:
        sh(["git", "clone", "--no-hardlinks", "--depth", "1", "-q",
            "https://github.com/%s.git" % REPO_SLUG, str(clone)])
        for k, v in (("user.name", "rob531"), ("user.email", "robin.craib@gmail.com")):
            sh(["git", "config", k, v], cwd=clone)
        _, base = sh(["git", "rev-parse", "--short", "HEAD"], cwd=clone)
        base = base.strip()
        branch = BRANCH_PREFIX + base
        sh(["git", "checkout", "-q", "-B", branch], cwd=clone)

        copied, missing = copy_in(runtime, clone, rels)
        if missing:
            print("\nREFUSED: %d candidate(s) vanished from the runtime between grading and copy: %s"
                  % (len(missing), missing))
            return 2
        staged, rc = stage_exact(clone, copied)
        if rc:
            print("\nREFUSED: the index does not equal the intended set -- NOT committing.")
            print("  intended: %s" % sorted(copied))
            print("  staged:   %s" % staged)
            return 1
        print("\nindex verified: %d path(s), exactly the intended set" % len(staged))

        msg = ("land stranded re-emissions: %d CLEAN module(s) from #4079\n\n"
               "These files were regenerated by the quarantine re-emission (#4070 -> #4625)\n"
               "and have been sitting UNTRACKED on %s since 2026-09-10..09-15 -- present on\n"
               "a disk, in no repository (established by cycle-0166, #5922).\n\n"
               "Each one is graded CLEAN by tools/stranded_review.py (#6077): it parses, and\n"
               "every table it names resolves on some catalog plane. Graded against %s,\n"
               "catalog %s. The %d PHANTOM candidate(s) in the same bucket are NOT in this PR\n"
               "and are refused by the lander by construction.\n\n"
               "Staged by explicit pathspec and the index asserted equal to the intended set;\n"
               "nothing was committed from the runtime tree, which carries ~18.5k untracked files.\n"
               % (len(staged), runtime, basis.get("repo_head"),
                  ",".join(basis.get("catalog_planes") or []), len(refused)))
        sh(["git", "commit", "-q", "-m", msg], cwd=clone)
        sh(["git", "push", "-q", "-u", "origin", branch], cwd=clone)

        title = "%s land %d CLEAN stranded re-emission(s) (#4079)" % (PR_MARKER, len(staged))
        body_path = work / "body.md"
        body_path.write_text(
            msg + "\nFiles:\n" + "".join("- `%s`\n" % p for p in staged) +
            "\nRefused in the same bucket (PHANTOM, named a table on no plane):\n" +
            "".join("- `%s`\n" % r["candidate"] for r in refused) +
            "\nOpened by `tools/stranded_land.py` (cycle-0177). The lander never grades, never\n"
            "deletes, never merges, and never force-pushes. `--self-test` carries six poles,\n"
            "including a decoy file that `git add -A` is observed catching and the explicit\n"
            "stager is observed excluding.\n", encoding="utf-8")
        _, out = sh(["gh", "pr", "create", "-R", REPO_SLUG, "--base", "main", "--head", branch,
                     "--title", title, "--body-file", str(body_path)])
        print(out.strip())
        print("\nLANDED-AS-PR: %d file(s) on %s. Merge on green CI; %d eligible remain."
              % (len(staged), branch, len(eligible) - len(staged)))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("\nAPPLY FAILED (nothing merged, nothing deleted): %s: %s" % (type(exc).__name__, exc))
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
