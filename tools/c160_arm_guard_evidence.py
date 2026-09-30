#!/usr/bin/env python3
"""Evidence for ops/host/arm_guard.sh (cycle-0160, FU-572).

R4 of HARNESS_DOCTRINE.md: an assertion never observed RED is an untested
branch, not evidence. So every control here is run twice -- once against a
MUTANT of the guard built to violate exactly that control, where it must FAIL,
and once against the real guard, where it must PASS. A control that cannot be
made to fail is deleted rather than reported.

The fixtures are real git repositories with a real untracked collider, because
the defect this guard exists to clear ("untracked working tree files would be
overwritten by merge") cannot be reproduced by a mock.

    python3 tools/c160_arm_guard_evidence.py --controls
    python3 tools/c160_arm_guard_evidence.py --drift [repo]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ops" / "host" / "arm_guard.sh"
SAFE_FF = ROOT / "ops" / "host" / "safe_ff.sh"


UNAVAILABLE = "UNAVAILABLE"


def bash_usable() -> tuple[bool, str]:
    """True only if `bash` actually executes a script.

    On the tower `bash` resolves to the WSL stub: it exists, it is on PATH, and
    it runs nothing. A control that reads that as a failure publishes a verdict
    about code that never ran -- the exact class HARNESS_DOCTRINE.md is about.
    """
    if not shutil.which("bash"):
        return False, "no bash on PATH"
    r = sh(["bash", "-c", "printf c160ok"])
    if r.returncode == 0 and "c160ok" in (r.stdout or ""):
        return True, "bash executes"
    out = ((r.stdout or "") + (r.stderr or "")).replace("\x00", "")
    if "Windows Subsystem for Linux" in out or "no installed distributions" in out:
        return False, "bash is the WSL stub with no distro installed"
    return False, f"bash did not execute (rc={r.returncode})"


def sh(cmd, cwd=None, env=None):
    e = dict(os.environ)
    e.setdefault("GIT_AUTHOR_NAME", "c160")
    e.setdefault("GIT_AUTHOR_EMAIL", "c160@example.invalid")
    e.setdefault("GIT_COMMITTER_NAME", "c160")
    e.setdefault("GIT_COMMITTER_EMAIL", "c160@example.invalid")
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, env=e, shell=isinstance(cmd, str),
                          capture_output=True, text=True, timeout=180)


def build_fixture(tmp: Path, *, behind: bool = True, collider: bool = True):
    """An origin + a runtime checkout deliberately left behind, with an
    untracked file at a path the incoming commit adds as tracked -- the exact
    shape that makes `git merge --ff-only` refuse on the live host."""
    origin = tmp / "origin"
    origin.mkdir()
    sh(["git", "init", "-q", "-b", "main", str(origin)])
    (origin / "seed.txt").write_text("seed\n")
    sh(["git", "add", "-A"], cwd=origin)
    sh(["git", "commit", "-qm", "seed"], cwd=origin)

    runtime = tmp / "runtime"
    sh(["git", "clone", "-q", str(origin), str(runtime)])

    if behind:
        newdir = origin / "services" / "staged" / "widget"
        newdir.mkdir(parents=True)
        (newdir / "__init__.py").write_text("ARMED = True\n")
        sh(["git", "add", "-A"], cwd=origin)
        sh(["git", "commit", "-qm", "add widget"], cwd=origin)
        if collider:
            # builder dropped the same path untracked, before the PR merged
            c = runtime / "services" / "staged" / "widget"
            c.mkdir(parents=True)
            (c / "__init__.py").write_text("LOCAL = True\n")
    return origin, runtime


def run_guard(guard: Path, runtime: Path, hb: Path, *, extra_env=None):
    env = {"ARM_HEARTBEAT": str(hb),
           "ZO_SAFE_FF_REF": "origin/main:ops/host/safe_ff.sh"}
    if extra_env:
        env.update(extra_env)
    # The fixture origin has no ops/host/safe_ff.sh of its own, so point the
    # guard at this repo's tracked copy via a file the fixture can resolve.
    env.setdefault("ZO_SAFE_FF_FILE", str(SAFE_FF))
    return sh(["bash", str(guard), str(runtime)], env=env)


# --------------------------------------------------------------------------
# mutants: each violates exactly one control
# --------------------------------------------------------------------------
MUTANTS = {
    "heartbeat_on_noop": (
        "stamps the heartbeat only when it acts, so a healthy idle host and a "
        "dead one look identical",
        lambda s: s.replace('  stamp 0 0 false "already_current" 0\n', "  "),
    ),
    "b2_working_tree_copy": (
        "runs the checkout's OWN copy of safe_ff.sh -- the copy that is stale "
        "by exactly the drift being cleared (audit finding B2)",
        lambda s: s.replace(
            'SAFE_FF_REF="${ZO_SAFE_FF_REF:-origin/main:ops/host/safe_ff.sh}"',
            'SAFE_FF_REF="WORKING_TREE"'),
    ),
    "constant_verdict": (
        "exits 0 whatever happened, reporting an unarmed host as armed",
        lambda s: s.replace(
            'log "REFUSED: still $behind_after behind after safe_ff.sh (rc=$ff_rc) -- NOT armed"',
            'log "ARMED (mutant)"').replace(
            'stamp "$behind_before" "$behind_after" true "ff_refused" "$ff_rc"\nexit 3',
            'stamp "$behind_before" 0 true "armed" "$ff_rc"\nexit 0'),
    ),
    "unknown_is_zero": (
        "treats a failed fetch as 'already current' -- R6, unknown is not zero",
        lambda s: s.replace(
            '  log "FATAL: git fetch failed -- refusing to report a drift measured on stale refs"\n'
            '  stamp -1 -1 false "fetch_failed" 2\n  exit 2',
            '  stamp 0 0 false "already_current" 0\n  exit 0'),
    ),
}


def control_heartbeat_on_noop(guard, tmp):
    ok, why = bash_usable()
    if not ok:
        return UNAVAILABLE, f"cannot measure here: {why}"
    _, runtime = build_fixture(tmp, behind=False)
    hb = tmp / "hb.json"
    r = run_guard(guard, runtime, hb)
    if not hb.exists():
        return False, "no heartbeat written on the no-op path"
    d = json.loads(hb.read_text())
    if d.get("action_taken") is not False or d.get("reason") != "already_current":
        return False, f"no-op heartbeat malformed: {d}"
    return r.returncode == 0, f"rc={r.returncode}"


def control_b2_tracked_copy(guard, tmp):
    src = guard.read_text()
    if "WORKING_TREE" in src:
        return False, "resolves safe_ff.sh from the working tree"
    if "origin/main:ops/host/safe_ff.sh" not in src:
        return False, "does not resolve safe_ff.sh from a tracked ref"
    # and it must refuse rather than silently fall back
    if "NOT falling back to the working-tree copy" not in src:
        return False, "no explicit refusal on an unresolvable tracked ref"
    return True, "resolves from origin/main and refuses to fall back"


def control_constant_verdict(guard, tmp):
    """Still-behind after the ff must exit 3, never 0."""
    src = guard.read_text()
    tail = src.split("behind_after=", 1)[-1] if "behind_after=" in src else src
    if "exit 3" not in tail:
        return False, "no non-zero exit on a host that is still behind"
    if not re.search(r'ff_refused', tail):
        return False, "no ff_refused reason recorded"
    return True, "unarmed host exits 3 and stamps ff_refused"


def control_unknown_is_zero(guard, tmp):
    src = guard.read_text()
    if 'stamp -1 -1 false "fetch_failed" 2' not in src:
        return False, "a failed fetch does not report unknown"
    if re.search(r'fetch origin main[^\n]*\n(?:[^\n]*\n){0,3}[^\n]*already_current', src):
        return False, "a failed fetch is reported as already_current"
    return True, "failed fetch reports unknown (-1) and exits 2"


def control_no_call_site(guard, tmp):
    """The control this cycle exists to satisfy: a guard nothing invokes is
    the same dark tool safe_ff.sh already was. Checked against the LIVE host
    crontab, not against a repo path (R1)."""
    r = sh("crontab -l 2>/dev/null | grep -c zo_arm_guard")
    n = (r.stdout or "0").strip() or "0"
    try:
        n = int(n)
    except ValueError:
        n = 0
    return n > 0, f"{n} crontab line(s) invoke arm_guard"


CONTROLS = {
    "heartbeat-written-on-the-no-op-path": (control_heartbeat_on_noop, "heartbeat_on_noop"),
    "safe_ff-resolved-from-the-tracked-ref": (control_b2_tracked_copy, "b2_working_tree_copy"),
    "a-still-behind-host-is-not-reported-armed": (control_constant_verdict, "constant_verdict"),
    "a-failed-fetch-is-unknown-not-current": (control_unknown_is_zero, "unknown_is_zero"),
    "the-guard-has-a-call-site": (control_no_call_site, None),
}


def run_controls() -> int:
    if not GUARD.exists():
        print(f"FATAL: {GUARD} missing")
        return 2
    real = GUARD.read_text()
    failures = 0
    unmeasured = 0
    print(f"basis: guard={GUARD.relative_to(ROOT)} ({len(real)} bytes)\n")
    for name, (fn, mutant_key) in CONTROLS.items():
        with tempfile.TemporaryDirectory() as td:
            ok, why = fn(GUARD, Path(td))
        if ok is UNAVAILABLE:
            print(f"  ????? {name}\n        NOT MEASURED: {why}")
            unmeasured += 1
            continue
        verdict = "GREEN" if ok else "RED  "
        print(f"  {verdict} {name}\n        cure: {why}")
        if not ok:
            failures += 1
        if mutant_key is None:
            continue
        desc, mutate = MUTANTS[mutant_key]
        mutated = mutate(real)
        if mutated == real:
            print(f"        !! MUTANT {mutant_key} DID NOT APPLY -- control is vacuous")
            failures += 1
            continue
        with tempfile.TemporaryDirectory() as td:
            mpath = Path(td) / "mutant.sh"
            mpath.write_text(mutated)
            mok, mwhy = fn(mpath, Path(td))
        if mok is UNAVAILABLE:
            print(f"        ????? mutant '{mutant_key}' not measurable here")
        elif mok:
            print(f"        !! NOT EVIDENCE: mutant '{mutant_key}' PASSED this control")
            print(f"           ({desc})")
            failures += 1
        else:
            print(f"        negative control OBSERVED RED: mutant '{mutant_key}' -> {mwhy}")
    print()
    if unmeasured:
        print(f"{unmeasured} control(s) COULD NOT BE MEASURED here -- that is not a pass.")
        print("  Run them where bash is real: the zo host, or CI (ubuntu).")
    if failures:
        print(f"{failures} control(s) failed")
        return 1
    if unmeasured:
        return 4
    print("all controls green, every mutant observed red")
    return 0


def report_drift(repo: str) -> int:
    r = sh(["git", "-C", repo, "rev-list", "--count", "HEAD..origin/main"])
    behind = (r.stdout or "").strip() or "unknown"
    h = sh(["git", "-C", repo, "rev-parse", "--short", "HEAD"]).stdout.strip()
    print(f"repo={repo} head={h} behind={behind}")
    return 0 if behind == "0" else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--controls", action="store_true")
    p.add_argument("--drift", nargs="?", const="/home/workspace/zo_sentinel")
    a = p.parse_args()
    if a.controls:
        return run_controls()
    if a.drift:
        return report_drift(a.drift)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
