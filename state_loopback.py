#!/usr/bin/env python3
"""
state_loopback.py -- crash-resilient multi-turn task contract (blueprint #4).

Implements the "stateful loop-back" the spec asks for, built entirely on
*local files + git* -- deliberately NOT on DuckDB and NOT on the network. The
whole point is decoupling: the execution loop's progress survives a container
collapse without ever taking a database lock or depending on write_service
being up. State lives in two files at the repo root:

    test-results.json   Default-FAIL manifest. Every directive/step starts as
                        FAIL. A step flips to PASS *only* when given explicit
                        verification proof. So a crash mid-run, a silent drop,
                        or a never-run step all read as FAIL -- you can never
                        mistake "didn't finish" for "passed" (this is the exact
                        ghost-completion class of bug goose_runner.py already
                        guards against, lifted to a first-class manifest).

    PROGRESS.md         Human-readable checkpoint log + a machine-readable
                        fenced JSON block holding the resume cursor.

After each meaningful step the loop calls commit_checkpoint(), which stages and
commits *only* these two state files. On spin-up, resume() parses PROGRESS.md
to pick up exactly where the previous (possibly killed) process left off.

Design constraints honoured:
  - No `import duckdb`, no write_service call: state is decoupled from the DB
    layer so DB lock storms / container restarts cannot corrupt or block it.
  - Idempotent: re-running init_manifest() preserves existing PASS results.
  - Safe in a non-git tree and when there is nothing to commit (no-ops, never
    raises) so the contract degrades gracefully off-host.

Paths are repo-root-relative, overridable with ZO_STATE_DIR for tests/CI.

CLI:
    python state_loopback.py init  step_a step_b step_c
    python state_loopback.py pass  step_b --proof "uv_gate_runner: PASS"
    python state_loopback.py fail  step_c --proof "Tier1 import failed"
    python state_loopback.py status
    python state_loopback.py resume
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("ZO_STATE_DIR", Path(__file__).resolve().parent))
RESULTS_FILE = STATE_DIR / "test-results.json"
PROGRESS_FILE = STATE_DIR / "PROGRESS.md"

PASS = "PASS"
FAIL = "FAIL"  # the default for every state until proven otherwise


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# test-results.json  --  the Default-FAIL manifest
# --------------------------------------------------------------------------- #
def _load_results() -> dict:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt/partial write -> treat as empty, Default-FAIL rebuilds it
    return {"contract": "default-fail", "updated_at": None, "steps": {}}


def _save_results(data: dict) -> None:
    data["updated_at"] = _utc()
    # write-then-replace so a crash mid-write can't leave a half file
    tmp = RESULTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(RESULTS_FILE)


def init_manifest(steps: list[str]) -> dict:
    """Seed the manifest. Unknown steps default to FAIL; existing results are
    preserved so re-init after a restart never silently un-passes work."""
    data = _load_results()
    for step in steps:
        data["steps"].setdefault(step, {
            "status": FAIL, "proof": None, "updated_at": _utc(),
        })
    _save_results(data)
    return data


def record_pass(step: str, proof: str) -> dict:
    """Flip a step to PASS -- ONLY with explicit verification proof. A blank
    proof is rejected, because an unproven PASS is exactly what the Default-FAIL
    contract exists to prevent."""
    if not proof or not proof.strip():
        raise ValueError("record_pass requires non-empty verification proof")
    data = _load_results()
    data["steps"][step] = {"status": PASS, "proof": proof.strip(), "updated_at": _utc()}
    _save_results(data)
    return data


def record_fail(step: str, proof: str = "") -> dict:
    data = _load_results()
    data["steps"][step] = {"status": FAIL, "proof": (proof or "").strip() or None,
                           "updated_at": _utc()}
    _save_results(data)
    return data


def record_gate_result(step: str, report: dict) -> dict:
    """Bridge from uv_gate_runner.run_gates() output to the manifest. PASS only
    when every gate passed; the gate detail string is the proof."""
    detail = "; ".join(f"T{g['tier']} {g['name']}={'P' if g['passed'] else 'F'}"
                       for g in report.get("gates", []))
    if report.get("passed"):
        return record_pass(step, f"uv_gate_runner: {detail}")
    return record_fail(step, f"uv_gate_runner: {detail}")


def summary() -> dict:
    data = _load_results()
    steps = data.get("steps", {})
    passed = [k for k, v in steps.items() if v.get("status") == PASS]
    failed = [k for k, v in steps.items() if v.get("status") != PASS]
    return {"total": len(steps), "passed": passed, "failed": failed,
            "all_pass": bool(steps) and not failed}


# --------------------------------------------------------------------------- #
# PROGRESS.md  --  the resume checkpoint
# --------------------------------------------------------------------------- #
_CURSOR_OPEN = "<!-- resume-cursor"
_CURSOR_CLOSE = "-->"


def checkpoint(step: str, note: str = "", cursor: dict | None = None) -> None:
    """Append a checkpoint line and refresh the machine-readable resume cursor.
    Called after each step so a killed container can resume from the last one."""
    cursor = cursor or {}
    cursor.setdefault("last_step", step)
    cursor.setdefault("at", _utc())

    body_lines = []
    if PROGRESS_FILE.exists():
        # drop any prior cursor block; keep the human log
        text = PROGRESS_FILE.read_text(encoding="utf-8")
        keep, skip = [], False
        for line in text.splitlines():
            if line.strip().startswith(_CURSOR_OPEN):
                skip = True
                continue
            if skip:
                if line.strip().endswith(_CURSOR_CLOSE):
                    skip = False
                continue
            keep.append(line)
        body_lines = keep
    else:
        body_lines = ["# PROGRESS", "",
                      "Crash-resilient checkpoint log for the Zo Sentinel build loop.",
                      "Managed by `state_loopback.py`. The fenced cursor at the bottom is",
                      "parsed on spin-up by `resume()` -- do not hand-edit it.", ""]

    body_lines.append(f"- [{_utc()}] **{step}** -- {note}" if note
                      else f"- [{_utc()}] **{step}**")
    cursor_block = (f"\n{_CURSOR_OPEN}\n{json.dumps(cursor, indent=2)}\n{_CURSOR_CLOSE}\n")
    PROGRESS_FILE.write_text("\n".join(body_lines).rstrip() + "\n" + cursor_block,
                             encoding="utf-8")


def resume() -> dict | None:
    """Parse the resume cursor on spin-up. Returns the cursor dict (with
    last_step) or None if there is no checkpoint yet -- a fresh start."""
    if not PROGRESS_FILE.exists():
        return None
    text = PROGRESS_FILE.read_text(encoding="utf-8")
    if _CURSOR_OPEN not in text:
        return None
    block = text.split(_CURSOR_OPEN, 1)[1].split(_CURSOR_CLOSE, 1)[0].strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# git  --  incremental commit of the state files (resume across a crash)
# --------------------------------------------------------------------------- #
def commit_checkpoint(message: str) -> bool:
    """Stage and commit ONLY the two state files. Returns True on a real commit,
    False on no-op (nothing changed / not a git tree). Never raises: a state
    commit must not be able to crash the build loop."""
    def _git(*args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(STATE_DIR),
                              capture_output=True, text=True, timeout=30)

    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return False
        paths = [p.name for p in (RESULTS_FILE, PROGRESS_FILE) if p.exists()]
        if not paths:
            return False
        _git("add", *paths)
        # nothing staged? (diff --cached --quiet exits 0 when no changes)
        if _git("diff", "--cached", "--quiet", "--", *paths).returncode == 0:
            return False
        return _git("commit", "-m", message, "--", *paths).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Default-FAIL state loop-back contract")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="seed steps as FAIL")
    p_init.add_argument("steps", nargs="+")
    p_init.add_argument("--commit", action="store_true")

    p_pass = sub.add_parser("pass", help="flip a step to PASS (needs proof)")
    p_pass.add_argument("step")
    p_pass.add_argument("--proof", required=True)
    p_pass.add_argument("--commit", action="store_true")

    p_fail = sub.add_parser("fail", help="record a step as FAIL")
    p_fail.add_argument("step")
    p_fail.add_argument("--proof", default="")
    p_fail.add_argument("--commit", action="store_true")

    sub.add_parser("status", help="print manifest summary")
    sub.add_parser("resume", help="print the resume cursor")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        init_manifest(args.steps)
        checkpoint("init", f"seeded {len(args.steps)} step(s) as Default-FAIL")
    elif args.cmd == "pass":
        record_pass(args.step, args.proof)
        checkpoint(args.step, f"PASS -- {args.proof}", cursor={"last_step": args.step})
    elif args.cmd == "fail":
        record_fail(args.step, args.proof)
        checkpoint(args.step, f"FAIL -- {args.proof}", cursor={"last_step": args.step})
    elif args.cmd == "status":
        print(json.dumps(summary(), indent=2))
        return 0
    elif args.cmd == "resume":
        print(json.dumps(resume(), indent=2))
        return 0

    if getattr(args, "commit", False):
        committed = commit_checkpoint(f"chore(state): checkpoint {args.cmd} "
                                      f"{getattr(args, 'step', '')}".strip())
        print(f"git checkpoint: {'committed' if committed else 'no-op'}")
    print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
