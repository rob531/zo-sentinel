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

AUTOPOIETIC UPGRADE (Argon-4 / Gemini 4 Neon, 2026-11-26):
  1. Emits scalar `gates_passed` (strictly `status == "PASS"` AND `returncode == 0`)
     and `gates_failed` in `deploy_candidate_verdict.json` / `verdict_latest.json`,
     unblocking Twin signal `GOVERNANCE.g_gates.blocking_ci_gates_passing`.
  2. Anti-Vanity Control (a): When `gates_run == 0` (empty `results` or all gates
     skipped, e.g. `--only typo`), fails closed to `verdict = "UNKNOWN"`,
     `gates_passed = None`, exit code `2` -- closing the prior vacuous-PASS bug
     (`"PASS" if not blocking else "FAIL"` returned `PASS` / `rc=0` on 0 gates!).
  3. Anti-Vanity Control (b): A gate record with `status == "PASS"` but non-zero or
     non-int `returncode` is classified as blocking/failed, never passed; read-only
     `--audit-verdict <path>` enforces operator `--allowed-root` containment before
     any disk read.

USAGE
-----
    git worktree add --detach D:\\zo\\_prod_dryrun <candidate-sha>
    cd D:\\zo\\_prod_dryrun
    python tools/verify_deploy_candidate.py            # human-readable
    python tools/verify_deploy_candidate.py --json     # verdict to stdout
    python tools/verify_deploy_candidate.py --skip pytest-evaluator
    python tools/verify_deploy_candidate.py --audit-verdict <path>
    python tools/verify_deploy_candidate.py --self-test

Exit code is 0 only when >=1 gate ran AND every non-skipped gate PASSes with
returncode == 0; 1 on FAIL; 2 on UNKNOWN (zero gates executed / unreadable audit).

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
import tempfile
import time
from pathlib import Path
from typing import Any

MODEL_NAME = "Gemini 4 Neon"

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "artifacts" / "deploy_candidate_verdict.json"
DEFAULT_ALLOWED_ROOTS = [Path(r"D:\zo")]

# Default per-gate wall-clock ceiling. The evaluator pytest set is the slow one;
# everything else finishes in seconds.
DEFAULT_TIMEOUT_S = 900

PY = sys.executable or "python"


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def normalize_allowed_roots(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        try:
            rp = r.resolve()
        except Exception:
            continue
        if rp not in out:
            out.append(rp)
    return out


def is_within_allowed_roots(candidate: Path, allowed_roots: list[Path]) -> tuple[bool, Path | None]:
    """Control (b): operator-anchored resolved-path containment check before disk I/O."""
    if not allowed_roots or not str(candidate).strip():
        return False, None
    try:
        resolved = candidate.resolve()
    except Exception:
        return False, None
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True, resolved
        except ValueError:
            continue
    return False, resolved


def _gate_truly_passed(res: dict[str, Any]) -> bool:
    """Control (b): a gate passes ONLY when status == 'PASS' AND returncode == 0."""
    return (
        isinstance(res, dict)
        and res.get("status") == "PASS"
        and _is_strict_int(res.get("returncode"))
        and res.get("returncode") == 0
    )


def summarize_gate_results(results: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Single deterministic verdict & scalar reducer shared by main() and --audit-verdict.

    Control (a): malformed/missing results or zero executed gates (`gates_run == 0`)
    resolves strictly to `verdict="UNKNOWN"`, `gates_passed=None`, `rc=2` — NEVER
    a vacuous `PASS` or fabricated `0`.
    """
    if not isinstance(results, list):
        return {
            "verdict": "UNKNOWN",
            "reason": "MALFORMED_RESULTS_LIST",
            "gates_run": 0,
            "gates_skipped": 0,
            "gates_passed": None,
            "gates_failed": None,
            "failing": [],
            "rc": 2,
        }

    skipped = [r for r in results if isinstance(r, dict) and r.get("status") == "SKIP"]
    executed = [r for r in results if not (isinstance(r, dict) and r.get("status") == "SKIP")]
    gates_run = len(executed)
    gates_skipped = len(skipped)

    if gates_run == 0:
        return {
            "verdict": "UNKNOWN",
            "reason": "ZERO_GATES_EXECUTED",
            "gates_run": 0,
            "gates_skipped": gates_skipped,
            "gates_passed": None,
            "gates_failed": None,
            "failing": [],
            "rc": 2,
        }

    passed = [r for r in executed if _gate_truly_passed(r)]
    blocking = [r for r in executed if not _gate_truly_passed(r)]
    failing_names = [
        str(r.get("gate") or "unknown_gate") if isinstance(r, dict) else "malformed_result"
        for r in blocking
    ]
    verdict = "PASS" if not blocking else "FAIL"
    return {
        "verdict": verdict,
        "reason": "" if verdict == "PASS" else f"blocking_gates={len(blocking)}",
        "gates_run": gates_run,
        "gates_skipped": gates_skipped,
        "gates_passed": len(passed),
        "gates_failed": len(blocking),
        "failing": failing_names,
        "rc": 0 if verdict == "PASS" else 1,
    }


def audit_existing_verdict(
    verdict_path: Path,
    allowed_roots: list[Path] | None = None,
) -> tuple[dict[str, Any], int]:
    """Read-only $0 audit of an existing verdict JSON file against Controls (a)+(b)."""
    roots = normalize_allowed_roots(allowed_roots if allowed_roots is not None else DEFAULT_ALLOWED_ROOTS)
    ok_p, resolved = is_within_allowed_roots(verdict_path, roots)
    if not ok_p or resolved is None:
        return {
            "status": "UNKNOWN",
            "reason": "TRUST_BOUNDARY_OUT_OF_SCOPE",
            "path": str(verdict_path),
            "model": MODEL_NAME,
        }, 2
    if not resolved.is_file():
        return {
            "status": "UNKNOWN",
            "reason": "VERDICT_FILE_NOT_FOUND",
            "path": str(resolved),
            "model": MODEL_NAME,
        }, 2
    try:
        blob = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "reason": f"VERDICT_JSON_PARSE_ERROR:{type(exc).__name__}",
            "path": str(resolved),
            "model": MODEL_NAME,
        }, 2
    if not isinstance(blob, dict):
        return {
            "status": "UNKNOWN",
            "reason": "VERDICT_NOT_JSON_OBJECT",
            "path": str(resolved),
            "model": MODEL_NAME,
        }, 2

    recomputed = summarize_gate_results(blob.get("results"))
    stored_gp = blob.get("gates_passed")
    stored_verdict = blob.get("verdict")
    discrepancies: list[str] = []
    if stored_gp is None:
        discrepancies.append(f"MISSING_GATES_PASSED_SCALAR(expected={recomputed['gates_passed']})")
    elif stored_gp != recomputed["gates_passed"]:
        discrepancies.append(f"GATES_PASSED_MISMATCH(stored={stored_gp},expected={recomputed['gates_passed']})")
    if stored_verdict != recomputed["verdict"]:
        discrepancies.append(f"VERDICT_MISMATCH(stored={stored_verdict},expected={recomputed['verdict']})")

    audit = {
        "status": "MEASURED" if recomputed["verdict"] != "UNKNOWN" else "UNKNOWN",
        "path": str(resolved),
        "model": MODEL_NAME,
        "head_sha": blob.get("head_sha"),
        "checked_utc": blob.get("checked_utc"),
        "stored_verdict": stored_verdict,
        "stored_gates_passed": stored_gp,
        "recomputed": recomputed,
        "discrepancies": discrepancies,
    }
    return audit, (2 if recomputed["rc"] == 2 else 0)


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
    """Paths git reports as modified/untracked, or None if git is unavailable."""
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


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            failures.append(f"{name}: {detail}")

    # Pole B1: Control (a) empty results or all-SKIP -> UNKNOWN rc=2, gates_passed=None (never vacuous PASS)
    s_emp = summarize_gate_results([])
    s_skip = summarize_gate_results([{"gate": "g1", "status": "SKIP", "returncode": None}])
    check(
        "B1_zero_gates_run_UNKNOWN_not_vacuous_PASS",
        s_emp["verdict"] == "UNKNOWN" and s_emp["gates_passed"] is None and s_emp["rc"] == 2
        and s_skip["verdict"] == "UNKNOWN" and s_skip["gates_passed"] is None and s_skip["rc"] == 2,
        f"emp={s_emp['verdict']}/{s_emp['rc']},skip={s_skip['verdict']}/{s_skip['rc']}",
    )

    # Pole B2: Control (b) contradictory status=PASS with returncode=1 cannot count as passed
    s_spoof = summarize_gate_results([{"gate": "g1", "status": "PASS", "returncode": 1}])
    check(
        "B2_status_pass_nonzero_rc_rejected",
        s_spoof["verdict"] == "FAIL" and s_spoof["gates_passed"] == 0 and s_spoof["failing"] == ["g1"] and s_spoof["rc"] == 1,
        str(s_spoof),
    )

    # Pole A1: Mixed 7 PASS + 1 FAIL -> verdict=FAIL, gates_run=8, gates_passed=7, gates_failed=1
    mixed = [{"gate": f"g{i}", "status": "PASS", "returncode": 0} for i in range(7)] + [
        {"gate": "schema-prm", "status": "FAIL", "returncode": 1}
    ]
    s_mix = summarize_gate_results(mixed)
    check(
        "A1_mixed_7_pass_1_fail_emits_gates_passed_7",
        s_mix["verdict"] == "FAIL" and s_mix["gates_run"] == 8 and s_mix["gates_passed"] == 7 and s_mix["gates_failed"] == 1 and s_mix["rc"] == 1,
        str(s_mix),
    )

    # Pole A2: All PASS rc=0 -> verdict=PASS, gates_passed=2, rc=0
    s_all = summarize_gate_results([
        {"gate": "g1", "status": "PASS", "returncode": 0},
        {"gate": "g2", "status": "PASS", "returncode": 0},
    ])
    check("A2_all_pass_GREEN", s_all["verdict"] == "PASS" and s_all["gates_passed"] == 2 and s_all["rc"] == 0, str(s_all))

    # Pole B3 + A3: audit_existing_verdict Control (b) OOB refusal + missing gates_passed detection
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        allowed = root / "allowed"
        outside = root / "outside"
        allowed.mkdir()
        outside.mkdir()
        oob = outside / "verdict.json"
        oob.write_text("{}", encoding="utf-8")
        a_oob, rc_oob = audit_existing_verdict(oob, [allowed])
        vf = allowed / "verdict_latest.json"
        vf.write_text(json.dumps({"verdict": "FAIL", "gates_run": 8, "gates_skipped": 0, "results": mixed}), encoding="utf-8")
        a_ok, rc_ok = audit_existing_verdict(vf, [allowed])
        check(
            "B3_A3_audit_oob_refused_and_missing_scalar_detected",
            rc_oob == 2 and a_oob["reason"] == "TRUST_BOUNDARY_OUT_OF_SCOPE"
            and rc_ok == 0 and a_ok["recomputed"]["gates_passed"] == 7
            and any("MISSING_GATES_PASSED_SCALAR" in d for d in a_ok["discrepancies"]),
            f"oob={a_oob['reason']},disc={a_ok['discrepancies']}",
        )

    print(f"SELF-TEST {'PASS' if not failures else 'FAIL'} (failed={len(failures)})")
    return 0 if not failures else 1


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
    ap.add_argument("--audit-verdict", type=Path, default=None,
                    help="read-only audit of an existing verdict JSON file")
    ap.add_argument("--allowed-root", action="append", type=Path, dest="allowed_roots")
    ap.add_argument("--self-test", action="store_true",
                    help="run hermetic two-pole self-test")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.audit_verdict is not None:
        roots = args.allowed_roots if args.allowed_roots else DEFAULT_ALLOWED_ROOTS
        audit, rc = audit_existing_verdict(args.audit_verdict, roots)
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return rc

    gates = build_gates()
    if args.only:
        gates = [g for g in gates if g.name in set(args.only)]
    skip = set(args.skip)

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

    summary = summarize_gate_results(results)
    dirty = None if dirty_before is None else bool(dirty_before)
    dirty_after = dirty_paths() or []
    gate_written = sorted(set(dirty_after) - set(dirty_before or []))
    verdict = {
        "verdict": summary["verdict"],
        "head_sha": head_sha(),
        "tree_sha": tree_sha(),
        "working_tree_dirty": dirty,
        "working_tree_dirty_paths": (dirty_before or [])[:50],
        "gate_written_paths": gate_written[:50],
        "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gates_run": summary["gates_run"],
        "gates_passed": summary["gates_passed"],
        "gates_failed": summary["gates_failed"],
        "gates_skipped": summary["gates_skipped"],
        "failing": summary["failing"],
        "results": results,
    }
    if dirty:
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
        print("verdict: {}  (head={} tree={} passed={}/{})".format(
            verdict["verdict"], (verdict["head_sha"] or "?")[:8],
            (verdict["tree_sha"] or "?")[:8],
            verdict["gates_passed"], verdict["gates_run"]))
        if summary["failing"]:
            print("  failing: {}".format(", ".join(verdict["failing"])))
            for r in results:
                if r.get("gate") in summary["failing"]:
                    print("")
                    print("--- {} ({}) ---".format(r["gate"], r["status"]))
                    print(r["tail"])
        if verdict.get("warning"):
            print("  WARNING: {}".format(verdict["warning"]))
        print("artifact: {}".format(ARTIFACT))

    return summary["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
