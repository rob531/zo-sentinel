# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
uv_gate_runner.py -- ephemeral, isolated execution gate (blueprint #1).

Runs the dynamic execution gates for a built artifact in a *throwaway*
environment so a bad build can never corrupt or destabilise the parent
container:

    Tier 0 (syntax)  -- ast.parse + py_compile, in-process, cheap, no deps.
    Tier 1 (import)  -- importlib smoke INSIDE a fresh `uv run --isolated`
                        interpreter, so a top-level side effect / missing dep
                        in the candidate cannot mutate this process's modules,
                        sys.path, or env, and cannot hold a file handle open.

Why uv and PEP 723 (per project_specification.md, blueprint runtime_isolation_uv):
  - This file carries inline PEP 723 script metadata above, so it is launched
    with `uv run tools/uv_gate_runner.py <target>` -- never a raw `python`
    binary against the shared, mutable container venv.
  - Tier 1 spawns `uv run --isolated --python 3.11` which builds an ephemeral,
    package-isolated interpreter per check. No shared site-packages, no lock on
    the parent venv, torn down on exit. That is the "ultra-fast isolated space"
    the blueprint calls for.

This gate is a PURE FUNCTION of the file on disk: it performs NO database
writes and opens NO DuckDB connection. It reports a verdict; persisting that
verdict to the Default-FAIL manifest is the caller's job (see
state_loopback.record_gate_result), keeping evaluation strictly read-only
(blueprint #4: "score compile steps without locking the disk layer").

Exit code: 0 iff every requested tier passed. Non-zero otherwise, so it slots
straight into CI / a `&&` chain.

Usage:
    uv run tools/uv_gate_runner.py path/to/built_file.py
    uv run tools/uv_gate_runner.py path/to/built_file.py --tier 0   # syntax only
    uv run tools/uv_gate_runner.py path/to/built_file.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path


def tier0_syntax(target: Path) -> dict:
    """Tier 0: syntax. ast.parse for a precise error location, then py_compile
    to catch anything the bytecode compiler rejects that the parser allowed."""
    try:
        source = target.read_text(encoding="utf-8")
    except OSError as e:
        return {"tier": 0, "name": "syntax", "passed": False, "detail": f"unreadable: {e}"}
    try:
        ast.parse(source, filename=str(target))
    except SyntaxError as e:
        return {"tier": 0, "name": "syntax", "passed": False,
                "detail": f"SyntaxError: {e.msg} at line {e.lineno}"}
    # py_compile to a temp cache so we never litter the source tree with .pyc
    with tempfile.TemporaryDirectory() as td:
        try:
            py_compile.compile(str(target), cfile=str(Path(td) / "out.pyc"),
                               doraise=True)
        except py_compile.PyCompileError as e:
            return {"tier": 0, "name": "syntax", "passed": False,
                    "detail": f"PyCompileError: {e}"}
    return {"tier": 0, "name": "syntax", "passed": True, "detail": "ast.parse + py_compile clean"}


# Importing the candidate as a module by file path, run as a one-liner so it can
# be handed to an isolated interpreter. {path} is json-quoted by the caller.
_IMPORT_PROBE = (
    "import importlib.util,sys;"
    "spec=importlib.util.spec_from_file_location('_gate_candidate',{path});"
    "mod=importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(mod);"
    "print('IMPORT_OK')"
)


def tier1_import(target: Path) -> dict:
    """Tier 1: import smoke in a fresh, package-isolated `uv run` interpreter.

    `--isolated` gives a clean, ephemeral environment so the candidate's
    top-level code runs nowhere near this process. If uv is unavailable we fall
    back to a plain subprocess (still process-isolated, just not venv-isolated)
    rather than skipping the gate."""
    probe = _IMPORT_PROBE.format(path=json.dumps(str(target)))
    uv_cmd = ["uv", "run", "--isolated", "--python", "3.11", "python", "-c", probe]
    plain_cmd = [sys.executable, "-c", probe]

    def _run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    try:
        proc = _run(uv_cmd)
        runner = "uv run --isolated"
    except FileNotFoundError:
        proc = _run(plain_cmd)
        runner = "subprocess fallback (uv not found)"
    except subprocess.TimeoutExpired:
        return {"tier": 1, "name": "import", "passed": False,
                "detail": "import probe timed out after 180s (likely blocking top-level code)"}

    if proc.returncode == 0 and "IMPORT_OK" in proc.stdout:
        return {"tier": 1, "name": "import", "passed": True, "detail": f"imported clean via {runner}"}
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = err[-1] if err else f"exit {proc.returncode}"
    return {"tier": 1, "name": "import", "passed": False,
            "detail": f"import failed via {runner}: {tail}"}


GATES = {0: tier0_syntax, 1: tier1_import}


def run_gates(target: Path, tiers: list[int]) -> dict:
    results = []
    for t in tiers:
        res = GATES[t](target)
        results.append(res)
        # Tier 0 is a precondition for Tier 1: a file that won't parse can't import.
        if not res["passed"] and t == 0 and 1 in tiers:
            results.append({"tier": 1, "name": "import", "passed": False,
                            "detail": "skipped: Tier 0 syntax failed"})
            break
    passed = all(r["passed"] for r in results)
    return {"target": str(target), "passed": passed, "gates": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="uv-isolated execution gates (Tier 0/1)")
    ap.add_argument("target", help="Python file to gate")
    ap.add_argument("--tier", type=int, choices=[0, 1], action="append",
                    help="run only this tier (repeatable); default runs both")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_file():
        print(f"GATE_ERROR: not a file: {target}", file=sys.stderr)
        return 2

    tiers = sorted(set(args.tier)) if args.tier else [0, 1]
    report = run_gates(target, tiers)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for g in report["gates"]:
            mark = "PASS" if g["passed"] else "FAIL"
            print(f"[{mark}] Tier {g['tier']} {g['name']}: {g['detail']}")
        print(f"=> {'PASS' if report['passed'] else 'FAIL'}: {target.name}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
