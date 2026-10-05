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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_SLUG = "rob531/zo-sentinel"

# This module is one of the quarantine manifest's MANAGERS, not a consumer of
# the modules it names. tools/requeue_quarantined.py reads the marker below and
# excludes this file from its live-referrer corpus. Without it, the self-test
# fixture further down -- which quotes two candidate filenames verbatim out of a
# CI log -- made one withdrawn module look WANTED and moved that tool's
# no_live_referrer count 45 -> 44, measured 2026-10-04 (cycle-0177) with a
# two-pole control on one tree: clean main passed, the same tree plus this file
# failed `assert 44 >= 45`.
QUARANTINE_MANAGER_MARK = "QUARANTINE-MANAGER: names candidates to manage them, not to use them"

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


def pkg_key(candidate: str) -> str:
    """The unit a landing must be atomic in.

    `services/staged/<name>/anything.py` is a PACKAGE: landing `contract.py`
    without the `__init__.py` beside it puts a half-package in the repo, which
    is an import error wearing a merge. Everything else is its own unit."""
    parts = candidate.split("/")
    if len(parts) >= 3 and parts[0] == "services" and parts[1] == "staged":
        return "/".join(parts[:3])
    return candidate


def group_units(rows):
    """Group the untracked rows into atomic units and decide each unit WHOLE.

    A unit is eligible only if EVERY untracked member of it grades CLEAN. One
    PHANTOM member refuses the whole unit: landing a package's clean half and
    leaving its phantom half behind is the 'one door of eight' shape, and it
    would also hand CI a package whose siblings are missing.

    Returns (units, refused_units) where each is a list of
    {"key", "rels", "bytes", "verdicts"} sorted by total bytes ascending."""
    buckets: dict[str, list] = {}
    for r in rows:
        if r.get("landed_state") != "untracked":
            continue
        buckets.setdefault(pkg_key(r["candidate"]), []).append(r)
    ok, bad = [], []
    for key, members in buckets.items():
        verdicts = sorted({m.get("verdict") for m in members})
        unit = {
            "key": key,
            "rels": sorted(m["candidate"] for m in members),
            "bytes": sum(m.get("bytes") or 0 for m in members),
            "verdicts": verdicts,
        }
        (bad if any(v in BAD_VERDICTS for v in verdicts) else ok).append(unit)
    ok.sort(key=lambda u: (u["bytes"], u["key"]))
    bad.sort(key=lambda u: u["key"])
    return ok, bad


def take_batch(units, budget: int):
    """Fill the batch with WHOLE units and never a fraction of one.

    A unit bigger than the whole budget is taken alone rather than skipped
    forever -- skipping it would starve it out of every future run, which is a
    rate limit that has quietly become a refusal."""
    batch, used = [], 0
    for u in units:
        n = len(u["rels"])
        if used and used + n > budget:
            continue
        if not used and n > budget:
            return [u]
        if used + n <= budget:
            batch.append(u)
            used += n
    return batch


def refill_after_gate(units, batch_units, inside, want, dropped_keys,
                      last_attempt: bool = False):
    """Recompute the batch after the hollow-scaffold gate rejected `inside`.

    Two things, and the first is the throughput defect this fixes:

    * REFILL, do not merely subtract. The first version dropped the rejected
      files out of the batch and pushed whatever was left, so the number of
      modules landed per run was set by the gate's reject rate rather than by
      MAX_PER_RUN. Observed on cycle-0184 / PR #6196: a budget of 8 landed 5
      while 30 eligible units sat waiting -- 3 of the 8 were hollow, nothing
      took their place, and the queue therefore needs ~1.6x as many runs as the
      rate limit implies. The negative control for this is pole P11b, which
      runs the old shrink-only arithmetic and must be seen to under-fill.

    * A rejected member refuses its WHOLE unit, exactly as group_units decides
      eligibility. Subtracting the named file alone could push a package's
      clean half with its siblings missing -- the 'one door of eight' shape.

    On the LAST attempt it shrinks instead of refilling: a run already red
    twice must converge, not keep pulling in never-gated units.

    Returns (batch_units, dropped_keys); never mutates its arguments.
    """
    dropped_keys = set(dropped_keys) | {
        u["key"] for u in units if any(r in inside for r in u["rels"])
    }
    pool = batch_units if last_attempt else units
    return (take_batch([u for u in pool if u["key"] not in dropped_keys], want),
            dropped_keys)


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


# ------------------------------------------------------------------- the gate

GATE_REL = "tests/ci/no_hollow_scaffold.py"


def run_gate(clone: Path):
    """Ask the REPO'S OWN added-file gate about the commit that now exists.

    This is here because of what #6106 measured: tools/stranded_review.py grades
    a file CLEAN when every table it names resolves on one of four catalog
    planes (63 tables). The repo's blocking gates ask different questions --
    no_hollow_scaffold asks whether the module has a real data layer, and
    capmap-check resolves tables against app.sql, not the planes. So a CLEAN
    grade is NOT a landability grade, and the first batch of 8 proved it: 2 were
    rejected (manual_override_api_v2.py, sentinel_ui_evidence_drawer_route.py)
    and took the other 6 down with them.

    The gate reads `git diff --diff-filter=A <BASE>...HEAD`, so it can only be
    asked AFTER the commit exists -- which is why this runs post-commit and the
    batch is rebuilt rather than un-staged.

    Returns (rc, rejected_rels, text). rc=2 means the gate was not resolvable,
    which is UNKNOWN and never a pass (R6)."""
    if not (clone / GATE_REL).is_file():
        return 2, [], "gate script absent at %s -- UNKNOWN, not clean" % GATE_REL
    env = dict(os.environ, BASE_REF="origin/main")
    r = subprocess.run([sys.executable, GATE_REL], cwd=clone,
                       capture_output=True, env=env, timeout=600)
    text = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode, parse_gate_rejects(text), text


def parse_gate_rejects(text: str):
    """Pull the rejected paths out of no_hollow_scaffold's own report lines.

    Its format, verbatim from the #6106 run:
        FAIL  [scaffold]  manual_override_api_v2.py  -- hollow scaffold: ...
    The bracketed rule name is skipped; the first path-looking token wins."""
    out = []
    for line in text.splitlines():
        t = line.strip()
        if not t.startswith("FAIL"):
            continue
        for tok in t.split():
            if tok.endswith(".py") and not tok.startswith("["):
                out.append(tok)
                break
    return sorted(set(out))


def build_commit(clone: Path, runtime: Path, branch: str, rels, msg: str):
    """Put exactly `rels` on a fresh `branch` off origin/main and commit.

    Re-callable: it resets the branch to origin/main first, so dropping a
    gate-rejected file is a rebuild, not an amend on top of a bad tree."""
    sh(["git", "checkout", "-q", "-B", branch, "origin/main"], cwd=clone)
    sh(["git", "clean", "-qfd"], cwd=clone, check=False)
    copied, missing = copy_in(runtime, clone, rels)
    if missing:
        return None, "candidate(s) vanished from the runtime between grading and copy: %s" % missing
    staged, rc = stage_exact(clone, copied)
    if rc:
        return None, ("the index does not equal the intended set -- NOT committing.\n"
                      "  intended: %s\n  staged:   %s" % (sorted(copied), staged))
    sh(["git", "commit", "-q", "-m", msg], cwd=clone)
    return staged, None


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

    # P7 -- a package is atomic. The naive byte-sorted FLAT batch is observed
    # splitting services/staged/overview_dashboard (RED); the grouped batch is
    # observed keeping it whole. This is the live shape from #4079: contract.py
    # is 5454B and sorts into the first 8 while its __init__.py (7643B) does not.
    pkg_rows = [
        {"candidate": "services/staged/overview_dashboard/contract.py",
         "landed_state": "untracked", "verdict": "CLEAN", "bytes": 5454},
        {"candidate": "services/staged/overview_dashboard/__init__.py",
         "landed_state": "untracked", "verdict": "CLEAN", "bytes": 7643},
        {"candidate": "services/staged/overview_dashboard/logic.py",
         "landed_state": "untracked", "verdict": "CLEAN", "bytes": 9288},
        {"candidate": "a1.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 100},
        {"candidate": "a2.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 200},
        {"candidate": "a3.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 300},
        {"candidate": "a4.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 400},
        {"candidate": "a5.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 500},
        {"candidate": "a6.py", "landed_state": "untracked", "verdict": "CLEAN", "bytes": 600},
        {"candidate": "services/staged/half_phantom/__init__.py",
         "landed_state": "untracked", "verdict": "PHANTOM", "bytes": 10},
        {"candidate": "services/staged/half_phantom/logic.py",
         "landed_state": "untracked", "verdict": "CLEAN", "bytes": 10},
    ]
    _, flat_eligible, _, _ = partition(pkg_rows)
    flat = [r["candidate"] for r in flat_eligible[:8]]
    flat_split = ("services/staged/overview_dashboard/contract.py" in flat and
                  "services/staged/overview_dashboard/__init__.py" not in flat)

    units, bad_units = group_units(pkg_rows)
    batch = take_batch(units, 8)
    rels = sorted(x for u in batch for x in u["rels"])
    od = [x for x in rels if x.startswith("services/staged/overview_dashboard/")]
    whole_or_absent = len(od) in (0, 3)
    pole("P7 package kept whole where a flat byte sort splits it",
         flat_split and whole_or_absent and len(rels) <= 8,
         "flat took %s ; grouped took %s" % (flat, rels))

    pole("P7b a unit with one PHANTOM member is refused WHOLE",
         [u["key"] for u in bad_units] == ["services/staged/half_phantom"] and
         not any(x.startswith("services/staged/half_phantom/") for u in units for x in u["rels"]),
         "refused units=%s" % [u["key"] for u in bad_units])

    pole("P7c a unit larger than the budget is taken alone, never starved",
         [u["key"] for u in take_batch(units, 2)] != [] and
         len(take_batch([u for u in units if u["key"].endswith("overview_dashboard")], 2)) == 1,
         "budget-2 batch=%s" % [u["key"] for u in take_batch(units, 2)])

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

    # P8 -- RUN main() END TO END on the plan path against a stub grader.
    # The first version of this file had 10 green poles and a NameError on line
    # 472 of the plan path, because not one pole executed main(). A self-test
    # that never runs the entry point is testing a different artifact than the
    # one that runs (R1), so this pole exists to execute it.
    tmp8 = Path(tempfile.mkdtemp(prefix="stranded_land_p8_"))
    try:
        stub = tmp8 / "stub_grader.py"
        stub.write_text(
            "import argparse, json, sys\n"
            "a = argparse.ArgumentParser()\n"
            "a.add_argument('--repo'); a.add_argument('--json'); a.add_argument('--manifest')\n"
            "a.add_argument('--enforce', action='store_true')\n"
            "n = a.parse_args()\n"
            "rows = [\n"
            "  {'candidate': 'u%d.py' % i, 'landed_state': 'untracked',\n"
            "   'verdict': 'CLEAN', 'bytes': 100 + i, 'lines': i} for i in range(5)\n"
            "] + [\n"
            "  {'candidate': 'services/staged/pkg/__init__.py', 'landed_state': 'untracked',\n"
            "   'verdict': 'CLEAN', 'bytes': 50, 'lines': 5},\n"
            "  {'candidate': 'services/staged/pkg/logic.py', 'landed_state': 'untracked',\n"
            "   'verdict': 'CLEAN', 'bytes': 60, 'lines': 6},\n"
            "  {'candidate': 'bad.py', 'landed_state': 'untracked', 'verdict': 'PHANTOM',\n"
            "   'bytes': 10, 'lines': 1},\n"
            "  {'candidate': 'done.py', 'landed_state': 'landed', 'verdict': 'CLEAN',\n"
            "   'bytes': 10, 'lines': 1},\n"
            "  {'candidate': 'gone.py', 'landed_state': 'absent', 'verdict': None,\n"
            "   'bytes': 0, 'lines': 0},\n"
            "]\n"
            "json.dump({'basis': {'repo_head': 'stub0000', 'catalog_planes': ['bus:1'],\n"
            "  'catalog_tables': 1, 'bus_age_days': 0.0, 'generated_at': 'stub'},\n"
            "  'counts': {}, 'verdicts': {}, 'rows': rows}, open(n.json, 'w'))\n",
            encoding="utf-8")
        jout = tmp8 / "p8.json"
        import io as _io
        import contextlib as _ctx
        buf = _io.StringIO()
        try:
            with _ctx.redirect_stdout(buf):
                rc8 = main(["--repo", str(tmp8), "--grader", str(stub),
                            "--json", str(jout), "--limit", "4"])
            boom = None
        except Exception as exc:  # noqa: BLE001 -- a raise here is the whole point
            rc8, boom = None, "%s: %s" % (type(exc).__name__, exc)
        text = buf.getvalue()
        payload = json.loads(jout.read_text(encoding="utf-8")) if jout.is_file() else {}
        pole("P8 main() runs the PLAN path without raising",
             boom is None and rc8 == 1,
             boom or ("rc=%s (1 expected)" % rc8))
        pole("P8b the plan batch is <= --limit and holds no refused row",
             boom is None and len(payload.get("batch") or []) <= 4
             and "bad.py" not in (payload.get("batch") or [])
             and "done.py" not in (payload.get("batch") or [])
             and "gone.py" not in (payload.get("batch") or []),
             "batch=%s" % (payload.get("batch"),))
        pole("P8c the plan prints its BASIS (R5), not just a verdict",
             "BASIS" in text and "stub0000" in text,
             "stdout head: %s" % text[:120].replace("\n", " | "))
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    # P9 -- the gate consult. Its parser is fed the REAL text #6106's no-hollow
    # job printed today, so the pole fails if the report format moves under us.
    real = (
        "HOLLOW FILE(S) DETECTED -- added modules that are not real:\n"
        "  FAIL  [scaffold]  manual_override_api_v2.py  -- hollow scaffold: standalone API"
        " with no real data layer (app.db/app.models) (no-hollow CI would reject)\n"
        "  FAIL  [scaffold]  sentinel_ui_evidence_drawer_route.py  -- hollow scaffold:"
        " standalone API with no real data layer (app.db/app.models) (no-hollow CI would reject)\n"
        "Real modules import the app data layer (app.db/app.models) and use no mock DB.\n")
    pole("P9 gate report parsed into exactly the two files it named",
         parse_gate_rejects(real) == ["manual_override_api_v2.py",
                                      "sentinel_ui_evidence_drawer_route.py"],
         "parsed %s" % parse_gate_rejects(real))

    pole("P9b a clean gate report yields no rejects",
         parse_gate_rejects("all added modules look real\nOK\n") == [],
         "parsed %s" % parse_gate_rejects("all added modules look real\nOK\n"))

    # P9c -- RED pole: an ABSENT gate is rc=2 UNKNOWN, never a silent pass. A
    # lander that treats a missing gate as clean would push exactly the batch
    # #6106 pushed.
    tmp9 = Path(tempfile.mkdtemp(prefix="stranded_land_p9_"))
    try:
        grc, rej, gtxt = run_gate(tmp9)
        pole("P9c an absent gate is rc=2, not clean",
             grc == 2 and rej == [] and GATE_REL in gtxt,
             "rc=%s text=%s" % (grc, gtxt[:90]))
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    # P10 -- the branch name must track the batch CONTENT. Naming it after the
    # base commit alone is what made the rebuilt 6-file batch collide with the
    # already-pushed 8-file branch and get refused at the push (observed on the
    # live run, 2026-10-04). Same batch -> same branch (idempotent no-op);
    # different batch -> different branch.
    def _d(rels):
        return hashlib.sha256("\n".join(sorted(rels)).encode()).hexdigest()[:8]
    eight = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py", "h.py"]
    six = eight[:6]
    pole("P10 branch digest is stable for one batch and differs across batches",
         _d(eight) == _d(list(reversed(eight))) and _d(eight) != _d(six),
         "d8=%s d8rev=%s d6=%s" % (_d(eight), _d(list(reversed(eight))), _d(six)))

    # P11 -- the gate's rejects must be REFILLED, not subtracted. This is the
    # cycle-0184 defect: PR #6196 landed 5 of a budget of 8 because 3 were
    # hollow and nothing replaced them, while 30 units were eligible.
    def _mku(k, n=1):
        return {"key": k, "rels": ["%s_%d.py" % (k, i) for i in range(n)],
                "bytes": 100, "verdicts": ["CLEAN"]}
    _allu = [_mku("a"), _mku("b"), _mku("c"), _mku("d")]
    _first = take_batch(_allu, 2)
    _rej = _first[0]["rels"][:1]
    _refilled, _dk = refill_after_gate(_allu, _first, _rej, 2, set())
    _shrunk = [x for x in _first if x["key"] not in _dk]
    pole("P11 a gate reject is REFILLED to the budget, not subtracted from it",
         len(_refilled) == 2 and "a" not in [x["key"] for x in _refilled],
         "refilled=%s" % [x["key"] for x in _refilled])

    # P11b -- NEGATIVE CONTROL. The old shrink-only arithmetic is run here and
    # must be OBSERVED under-filling. If this pole ever goes green, P11 is
    # measuring nothing (R4).
    pole("P11b shrink-only observed RED: 1 unit of a 2-unit budget",
         len(_shrunk) == 1, "shrunk to %d, expected 1" % len(_shrunk))

    # P11c -- a rejected member refuses its WHOLE unit; never half a package.
    _pk = {"key": "services/staged/p",
           "rels": ["services/staged/p/__init__.py", "services/staged/p/x.py"],
           "bytes": 10, "verdicts": ["CLEAN"]}
    _b2, _dk2 = refill_after_gate([_pk, _mku("z")], [_pk],
                                  ["services/staged/p/x.py"], 2, set())
    pole("P11c a rejected member refuses the whole unit, never half a package",
         "services/staged/p" in _dk2
         and all(x["key"] != "services/staged/p" for x in _b2),
         "dropped=%s batch=%s" % (sorted(_dk2), [x["key"] for x in _b2]))

    # P11d -- the final attempt converges: it shrinks rather than refilling.
    _b3, _ = refill_after_gate(_allu, _first, _rej, 2, set(), last_attempt=True)
    pole("P11d the final attempt shrinks rather than refilling",
         [x["key"] for x in _b3] == ["b"], "%s" % [x["key"] for x in _b3])

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

    units, bad_units = group_units(rows)
    batch_units = take_batch(units, want)
    rels = sorted(x for u in batch_units for x in u["rels"])
    bl = {r["candidate"]: r for r in rows}
    print("")
    print("  atomic units eligible  %4d   (a services/staged/<name>/ package lands whole or not at all)"
          % len(units))
    print("  units refused whole    %4d   %s" % (len(bad_units), [u["key"] for u in bad_units]))
    print("")
    for u in batch_units:
        print("  unit: %s  (%d file(s), %dB)" % (u["key"], len(u["rels"]), u["bytes"]))
        for x in u["rels"]:
            r = bl.get(x, {})
            print("        %-58s %6dB %4d lines" % (x, r.get("bytes") or 0, r.get("lines") or 0))

    if a.json_out:
        Path(a.json_out).write_text(json.dumps({
            "basis": basis, "counts": {"candidates": len(rows), "landed": len(landed),
            "eligible": len(eligible), "refused": len(refused), "absent": len(absent)},
            "refused": [r["candidate"] for r in refused],
            "batch": rels, "in_flight": nf,
            "units": [u["key"] for u in units],
            "units_refused_whole": [u["key"] for u in bad_units],
        }, indent=2), encoding="utf-8")

    if not units:
        print("\nNOTHING ELIGIBLE. rc=0.")
        return 0
    if not batch_units:
        print("\n--limit 0: nothing requested. rc=1 (there IS work).")
        return 1

    # The PLAN is read-only and must stay readable even when `gh` is down. The
    # in-flight ceiling gates --apply ONLY. Ordering these the other way round
    # made a broken `gh` return rc=2 CANNOT EVALUATE for a question that needs
    # no network at all -- caught by pole P8.
    if not a.apply:
        print("\nPLAN ONLY (rc=1). Re-run with --apply to open ONE PR for the %d file(s) above." % len(rels))
        return 1

    if nf < 0:
        print("\nREFUSED: --apply needs the in-flight PR count and it could not be read."
              " An unknown is not a zero (R6). rc=2.")
        return 2
    if nf >= MAX_IN_FLIGHT:
        print("\nHELD: %d [stranded-land] PR(s) already open (ceiling %d). Land or close those first: %s"
              % (nf, MAX_IN_FLIGHT, ", ".join("#%d" % p["number"] for p in prs)))
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

        def message(n, dropped):
            m = ("land stranded re-emissions: %d CLEAN module(s) from #4079\n\n"
                 "These files were regenerated by the quarantine re-emission (#4070 -> #4625)\n"
                 "and have been sitting UNTRACKED on %s since 2026-09-10..09-15 -- present on\n"
                 "a disk, in no repository (established by cycle-0166, #5922).\n\n"
                 "Each one is graded CLEAN by tools/stranded_review.py (#6077): it parses, and\n"
                 "every table it names resolves on some catalog plane. Graded against %s,\n"
                 "catalog %s. The %d PHANTOM candidate(s) in the same bucket are NOT here and\n"
                 "are refused by the lander by construction.\n\n"
                 "Staged by explicit pathspec and the index asserted equal to the intended set;\n"
                 "nothing was committed from the runtime tree, which carries ~18.5k untracked files.\n"
                 % (n, runtime, basis.get("repo_head"),
                    ",".join(basis.get("catalog_planes") or []), len(refused)))
            if dropped:
                m += ("\nDROPPED by %s before the push, because a CLEAN grade is not a\n"
                      "landability grade -- the grader resolves tables against 4 catalog planes\n"
                      "and this gate asks whether the module has a real data layer:\n%s\n"
                      % (GATE_REL, "".join("  - %s\n" % d for d in dropped)))
            return m

        want_rels, dropped, gate_text = list(rels), [], ""
        gate_dropped_keys = set()
        for attempt in (1, 2, 3):
            staged, why = build_commit(clone, runtime, "_stranded_land_wip", want_rels,
                                       message(len(want_rels), dropped))
            if staged is None:
                print("\nREFUSED: %s" % why)
                return 1 if "index" in why else 2
            print("\nindex verified: %d path(s), exactly the intended set" % len(staged))
            grc, rej, gate_text = run_gate(clone)
            print("gate %s (attempt %d): rc=%d, rejects %s" % (GATE_REL, attempt, grc, rej or "nothing"))
            if grc == 0:
                break
            if grc == 2:
                print("\nREFUSED: %s" % gate_text.strip()[:400])
                print("An unrun gate is UNKNOWN, not clean (R6). rc=2.")
                return 2
            if not rej:
                print("\nREFUSED: the gate is RED but named no file, so there is nothing to drop.")
                print(gate_text.strip()[-800:])
                return 1
            inside = [r for r in rej if r in want_rels]
            if not inside:
                print("\nREFUSED: the gate rejects %s, which this batch did not add. Not ours to fix." % rej)
                return 1
            dropped += inside
            batch_units, gate_dropped_keys = refill_after_gate(
                units, batch_units, inside, want, gate_dropped_keys,
                last_attempt=(attempt == 3))
            want_rels = sorted(x for u in batch_units for x in u["rels"])
            if not want_rels:
                print("\nREFUSED: every file in this batch is rejected by %s. Nothing to land." % GATE_REL)
                print("  rejected: %s" % dropped)
                return 1
            print("  dropping unit(s) %s and %s the batch from origin/main: %d file(s) of a %d budget"
                  % (sorted(gate_dropped_keys),
                     "shrinking" if attempt == 3 else "REFILLING",
                     len(want_rels), want))
        else:
            print("\nREFUSED: %s still RED after dropping %s. Not pushing a red batch." % (GATE_REL, dropped))
            print(gate_text.strip()[-800:])
            return 1

        msg = message(len(staged), dropped)

        # The branch name tracks the batch CONTENT, not just the base commit.
        # The first version named it after the base alone, so when the gate
        # dropped two files and rebuilt the batch, the push landed on a branch
        # that already carried the 8-file commit and was REFUSED -- correctly,
        # because this tool never force-pushes. A different batch is a different
        # branch; the SAME batch is the same branch, which keeps re-running a
        # no-op instead of opening a duplicate PR.
        digest = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()[:8]
        branch = "%s%s-%s" % (BRANCH_PREFIX, base, digest)
        sh(["git", "branch", "-q", "-M", branch], cwd=clone)

        _, lsr = sh(["git", "ls-remote", "--heads", "origin", branch], cwd=clone, check=False)
        if branch in lsr:
            print("\nALREADY PUSHED: origin/%s exists and this tool never force-pushes." % branch)
            print("  This exact batch is already on the remote -- idempotent no-op, not a failure.")
            _, pl = sh(["gh", "pr", "list", "-R", REPO_SLUG, "--head", branch, "--state", "all",
                        "--json", "number,state,url"], cwd=clone, check=False)
            print("  its PR(s): %s" % pl.strip())
            return 1
        sh(["git", "push", "-q", "-u", "origin", branch], cwd=clone)

        title = "%s land %d stranded re-emission(s), gate-cleared (#4079)" % (PR_MARKER, len(staged))
        body_path = work / "body.md"
        body_path.write_text(
            msg + "\nFiles:\n" + "".join("- `%s`\n" % p for p in staged) +
            "\nRefused in the same bucket (PHANTOM, named a table on no plane):\n" +
            "".join("- `%s`\n" % r["candidate"] for r in refused) +
            ("\nDropped before the push by `%s`:\n" % GATE_REL) +
            ("".join("- `%s`\n" % d for d in dropped) if dropped else "- none\n") +
            "\nOpened by `tools/stranded_land.py` (cycle-0177). The lander never grades, never\n"
            "deletes, never merges, and never force-pushes. It asks the repo's own added-file\n"
            "gate BEFORE pushing and drops what that gate rejects, because a CLEAN referent\n"
            "grade is not a landability grade -- #6106 measured that.\n", encoding="utf-8")
        _, out = sh(["gh", "pr", "create", "-R", REPO_SLUG, "--base", "main", "--head", branch,
                     "--title", title, "--body-file", str(body_path)])
        print(out.strip())
        print("\nLANDED-AS-PR: %d file(s) on %s (dropped %d by the gate). Merge on green CI; "
              "%d eligible remain." % (len(staged), branch, len(dropped), len(eligible) - len(staged)))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("\nAPPLY FAILED (nothing merged, nothing deleted): %s: %s" % (type(exc).__name__, exc))
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
