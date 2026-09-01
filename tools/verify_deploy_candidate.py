#!/usr/bin/env python3
"""Run the blocking PR gates against the CURRENT WORKING TREE.

WHY THIS EXISTS
---------------
`pr-gates.yml`, `no-hollow.yml` and `schema-prm.yml` are wired to
`on: pull_request` only. GitHub evaluates them against the PR *head*, which for
a fast-moving `main` is frequently NOT the tree that ends up on `main`: a squash
merge onto a base the branch never saw produces a third tree that no gate has
ever executed.

Concretely, on 2026-07-27 the deploy candidate `84ca738d` (main HEAD) carried a
tree SHA of `57293874...` while its green PR head (#2037, `1c257819`) carried
`a54753c7...`. Every required check was green -- on a tree that was never
deployed. `main` itself had only CodeQL + a push workflow, none of them gates.

That gap sits directly under the prod release path: `prod-drift-sentinel` stages
a deploy at `main` HEAD, so the tree it stages is precisely the tree the gates
skipped. This module closes it by running the same commands the gates run,
against whatever tree is checked out, and emitting a machine-readable verdict.

USAGE
-----
    git worktree add --detach D:\\zo\\_prod_dryrun <candidate-sha>
    cd D:\\zo\\_prod_dryrun
    python tools/verify_deploy_candidate.py            # human-readable
    python tools/verify_deploy_candidate.py --json     # verdict to stdout
    python tools/verify_deploy_candidate.py --skip pytest-evaluator

Exit code is 0 only when every non-skipped gate PASSes. `artifacts/
deploy_candidate_verdict.json` is written on every run, pass or fail, so a
scheduled task can read a verdict it did not itself produce.

Stdlib only, read-only, no network, no host services, $0. Safe to re-run: it
overwrites its own artifact and touches nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "artifacts" / "deploy_candidate_verdict.json"

# Default per-gate wall-clock ceiling. The evaluator pytest set is the slow one;
# everything else finishes in seconds.
DEFAULT_TIMEOUT_S = 900

PY = sys.executable or "python"


class Gate:
    """One blocking check, named after the GitHub job it mirrors."""

    def __init__(self, name, workflow, argv, timeout=DEFAULT_TIMEOUT_S, needs=()):
        self.name = name
        self.workflow = workflow
        self.argv = argv
        self.timeout = timeout
        self.needs = tuple(needs)

    def missing_prereq(self):
        """Return a reason string if this gate cannot run here, else None."""
        for path in self.needs:
            if not (REPO_ROOT / path).exists():
                return "missing path: {}".format(path)
        exe = self.argv[0]
        if exe != PY and shutil.which(exe) is None:
            return "executable not on PATH: {}".format(exe)
        return None


def evaluator_pytest_targets():
    """Parse the test file list out of evaluator.yml rather than duplicating it.

    Keeping the list in one place means a test added to CI is picked up here
    automatically instead of silently drifting out of the candidate check.
    """
    wf = REPO_ROOT / ".github" / "workflows" / "evaluator.yml"
    if not wf.exists():
        return []
    text = wf.read_text(encoding="utf-8", errors="replace")
    marker = "python -m pytest"
    idx = text.find(marker)
    if idx == -1:
        return []
    targets = []
    for raw in text[idx + len(marker):].splitlines():
        line = raw.strip()
        if not line:
            continue
        # The run: block is a backslash-continued shell command; the first line
        # that is not a test path (or its continuation) ends the block.
        candidate = line.rstrip("\\").strip()
        if candidate.startswith("tests/") and candidate.endswith(".py"):
            if (REPO_ROOT / candidate).exists() and candidate not in targets:
                targets.append(candidate)
            continue
        if candidate.startswith("#"):
            continue
        break
    return targets


def build_gates():
    gates = [
        Gate(
            "static-analysis",
            "pr-gates.yml",
            [PY, "-m", "ruff", "check", "--select", "F,E9",
             "zo_sentinel", "tests/ci", "tests/gates"],
            timeout=300,
            needs=["zo_sentinel", "tests/ci"],
        ),
        Gate(
            "smoke-ladder",
            "pr-gates.yml",
            [PY, "-u", "-m", "tests.ci.run_ci_smoke"],
            needs=["tests/ci/run_ci_smoke.py"],
        ),
        Gate(
            "capmap-check",
            "pr-gates.yml",
            [PY, "-u", "tools/pull_check.py"],
            timeout=600,
            needs=["tools/pull_check.py"],
        ),
        Gate(
            "reachability-ratchet",
            "pr-gates.yml",
            [PY, "-u", "tools/reachability_ratchet.py", "--enforce"],
            timeout=600,
            needs=["tools/reachability_ratchet.py"],
        ),
        Gate(
            "no-hollow",
            "no-hollow.yml",
            [PY, "-u", "tests/ci/no_hollow_scaffold.py"],
            timeout=600,
            needs=["tests/ci/no_hollow_scaffold.py"],
        ),
        Gate(
            "schema-prm",
            "schema-prm.yml",
            [PY, "-u", "tests/ci/schema_prm_check.py"],
            timeout=600,
            needs=["tests/ci/schema_prm_check.py"],
        ),
        Gate(
            "dockerfile-copy-list",
            "evaluator.yml",
            [PY, "-u", "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "tests/test_dockerfile_copy_covers_active_services.py"],
            timeout=300,
            needs=["tests/test_dockerfile_copy_covers_active_services.py"],
        ),
        # fly.toml runs `alembic upgrade head` as the prod release_command.
        # Two branch heads in the revision graph abort that command against a
        # Postgres with no migration rollback (the v61 class) -- and that state
        # is reachable with NO migration file modified, so the sentinel's
        # file-path diff structurally cannot see it. Named separately from
        # pytest-evaluator because pytest-evaluator is SKIPPED on the tower
        # (test_ladder_routing is not host-reproducible), and the tower is
        # exactly where a deploy gets staged.
        Gate(
            "migration-head",
            "evaluator.yml",
            [PY, "-u", "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "tests/test_migration_graph_single_head.py"],
            timeout=300,
            needs=["tests/test_migration_graph_single_head.py",
                   "migrations/versions"],
        ),
    ]
    targets = evaluator_pytest_targets()
    if targets:
        gates.append(
            Gate(
                "pytest-evaluator",
                "evaluator.yml",
                [PY, "-u", "-m", "pytest", "-q", "-p", "no:cacheprovider"] + targets,
                needs=["tests"],
            )
        )
    return gates


def run_gate(gate, verbose):
    reason = gate.missing_prereq()
    if reason is not None:
        return {
            "gate": gate.name,
            "workflow": gate.workflow,
            "status": "SKIP",
            "reason": reason,
            "returncode": None,
            "duration_s": 0.0,
            "tail": "",
        }

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Gates are hermetic by contract; make that explicit so a gate that quietly
    # started depending on the host surfaces here rather than on the runner.
    env.setdefault("CI", "1")

    started = time.time()
    try:
        proc = subprocess.run(
            gate.argv,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=gate.timeout,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        rc = proc.returncode
        status = "PASS" if rc == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        out = (exc.output or b"").decode("utf-8", errors="replace")
        rc = None
        status = "TIMEOUT"

    duration = round(time.time() - started, 1)
    tail = "\n".join(out.strip().splitlines()[-25:])
    if verbose:
        print(tail)
    return {
        "gate": gate.name,
        "workflow": gate.workflow,
        "status": status,
        "reason": None,
        "returncode": rc,
        "duration_s": duration,
        "tail": tail,
    }


def head_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return out.stdout.decode().strip() or None
    except Exception:
        return None


def tree_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return out.stdout.decode().strip() or None
    except Exception:
        return None


def dirty_paths():
    """Paths git reports as modified/untracked, or None if git is unavailable.

    Returns a list so callers can say WHICH files are dirty -- "dirty: true"
    with no path list sent a prod-drift run chasing a phantom.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return [ln[3:].strip() for ln in out.stdout.decode().splitlines() if ln.strip()]
    except Exception:
        return None


def working_tree_dirty():
    paths = dirty_paths()
    return None if paths is None else bool(paths)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true",
                    help="print the verdict JSON to stdout instead of a table")
    ap.add_argument("--skip", action="append", default=[], metavar="GATE",
                    help="gate name to skip (repeatable), e.g. pytest-evaluator")
    ap.add_argument("--only", action="append", default=[], metavar="GATE",
                    help="run only these gates (repeatable)")
    ap.add_argument("--verbose", action="store_true",
                    help="echo each gate's output tail as it runs")
    args = ap.parse_args(argv)

    gates = build_gates()
    if args.only:
        gates = [g for g in gates if g.name in set(args.only)]
    skip = set(args.skip)

    # Snapshot dirtiness BEFORE any gate runs. Gates write tracked artifacts
    # (e.g. artifacts/ci_smoke_junit.xml from smoke-ladder), so measuring
    # afterwards reports DIRTY on a pristine checkout every single time and
    # trains the reader to ignore the one warning that matters.
    dirty_before = dirty_paths()

    results = []
    for gate in gates:
        if gate.name in skip:
            results.append({
                "gate": gate.name, "workflow": gate.workflow, "status": "SKIP",
                "reason": "skipped by --skip", "returncode": None,
                "duration_s": 0.0, "tail": "",
            })
            if not args.json:
                print("  SKIP {}".format(gate.name))
            continue
        if not args.json:
            print("  .... {}".format(gate.name), flush=True)
        res = run_gate(gate, args.verbose and not args.json)
        results.append(res)
        if not args.json:
            print("  {:<7} {}  ({}s)".format(res["status"], gate.name,
                                             res["duration_s"]), flush=True)

    blocking = [r for r in results if r["status"] not in ("PASS", "SKIP")]
    dirty = None if dirty_before is None else bool(dirty_before)
    dirty_after = dirty_paths() or []
    gate_written = sorted(set(dirty_after) - set(dirty_before or []))
    verdict = {
        "verdict": "PASS" if not blocking else "FAIL",
        "head_sha": head_sha(),
        "tree_sha": tree_sha(),
        "working_tree_dirty": dirty,
        "working_tree_dirty_paths": (dirty_before or [])[:50],
        "gate_written_paths": gate_written[:50],
        "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gates_run": len([r for r in results if r["status"] != "SKIP"]),
        "gates_skipped": len([r for r in results if r["status"] == "SKIP"]),
        "failing": [r["gate"] for r in blocking],
        "results": results,
    }
    if dirty:
        # A dirty tree means the verdict does not describe head_sha. Say so
        # rather than letting a caller attribute the result to a commit.
        verdict["warning"] = (
            "working tree was DIRTY BEFORE the gates ran -- this verdict "
            "describes the files on disk, not commit {}. Dirty: {}".format(
                verdict["head_sha"], ", ".join((dirty_before or [])[:10]) or "?")
        )

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print("")
        print("verdict: {}  (head={} tree={})".format(
            verdict["verdict"], (verdict["head_sha"] or "?")[:8],
            (verdict["tree_sha"] or "?")[:8]))
        if blocking:
            print("  failing: {}".format(", ".join(verdict["failing"])))
            for r in blocking:
                print("")
                print("--- {} ({}) ---".format(r["gate"], r["status"]))
                print(r["tail"])
        if verdict.get("warning"):
            print("  WARNING: {}".format(verdict["warning"]))
        print("artifact: {}".format(ARTIFACT))

    return 0 if not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
