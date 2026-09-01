#!/usr/bin/env python3
"""Run every FU's acceptance predicate; auto-close on green, auto-REOPEN on red.

This is the piece that turns FOLLOWUPS.md from a diary into a regression
suite. Each `- verify:` is a command that exits 0 IFF that follow-up is
fixed. This runner executes them on a cadence and writes the verdict back
into the ledger.

    GREEN x N (default 2, on separate runs)  -> status flips to `resolved`
    RED x 1 against a resolved/done entry    -> status flips back to `open`

The asymmetry is deliberate. Closing something requires repeated evidence;
re-opening it requires a single failure. The system should be biased toward
believing a problem is still present, because the recorded failure mode here
is regression of solved problems, not excessive caution.

Guard rails, each earned from a specific scar:

  * `verify_seen_red` gate. A predicate never observed failing cannot close
    anything. Greens from a NEVER-red verify are recorded and explicitly NOT
    acted on. (The goose-canary passed for days testing a transport the mesh
    does not use.)
  * Forbidden-token refusal. A verify is a read-only probe. Anything that
    could mutate state, push, deploy, or fire a paid resource is refused
    before execution, not sandboxed.
  * Per-command timeout, and a wall-clock ceiling for the whole sweep, so an
    unbounded probe cannot become the outage. (A snapshot that ran 11m30s
    grew to 64min+ on 0.19% more data; a watcher once killed a healthy
    backup.)
  * Backup before every ledger write, and re-parse to prove convergence.
  * `--dry-run` prints the diff it would make and writes nothing.

Usage
    python fu_verify.py --once            # one sweep, write results
    python fu_verify.py --once --dry-run  # show verdicts, touch nothing
    python fu_verify.py --negative-control  # prove the runner can report RED
    python fu_verify.py --json            # machine-readable, for a task to read
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fu_ledger  # noqa: E402
import fu_lock  # noqa: E402

DEFAULT_LEDGER = r"D:\zo\Zocomputer Agents\FOLLOWUPS.md"
BACKUP_ROOT = r"D:\zo\Zocomputer Agents\_followup_backups"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fu_verify_state.json")

GREENS_TO_CLOSE = 2
CMD_TIMEOUT_S = 60
SWEEP_CEILING_S = 900
CLOSED_STATES = ("resolved", "done")

# Shell messages that mean "I could not run this", across cmd.exe, PowerShell
# and sh. Needed because cmd.exe reports a missing binary as rc=1.
NOT_FOUND_HINTS = (
    "is not recognized as an internal or external command",
    "command not found",
    "no such file or directory",
    "cannot find the path",
    "cannot find the file specified",   # cmd.exe, seen misreporting FU-156 as RED
    "the syntax of the command is incorrect",  # malformed predicate, not a finding
    "the term '",                      # PowerShell: The term 'x' is not recognized
)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            pass
    return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_probe(cmd: str, timeout: int = CMD_TIMEOUT_S) -> dict:
    """Execute a verify.

    Exit-code contract -- three states, not two:

        0        GREEN    the probe ran and the FU is fixed
        1        RED      the probe ran and the FU is still broken
        >=2      UNKNOWN  the probe could NOT evaluate (bus down, binary
                          missing, bad SQL, network refused, timeout)

    The third state is the whole point. On the first live sweep 13 predicates
    came back non-zero because the write-service bus was not listening -- not
    because the bugs were present. Collapsing "could not evaluate" into "still
    broken" is the same lie as a gate that skips and reports as a gate that
    passes. UNKNOWN never stamps `verify_seen_red`, never closes, never
    reopens; it is surfaced for a human to fix the probe.
    """
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        rc, out, err = p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return {"verdict": "UNKNOWN", "rc": None, "secs": round(time.time() - t0, 1),
                "note": "TIMEOUT after %ss -- probe could not answer" % timeout, "tail": ""}
    except OSError as exc:
        return {"verdict": "UNKNOWN", "rc": None, "secs": round(time.time() - t0, 1),
                "note": "could not execute: %s" % exc, "tail": ""}
    tail = ((out + err).strip().replace("\n", " | "))[-240:]
    verdict = "GREEN" if rc == 0 else ("RED" if rc == 1 else "UNKNOWN")

    # cmd.exe returns rc=1 for a command it cannot find, which is
    # indistinguishable from "the predicate ran and the bug is present". A
    # typo'd probe would therefore read as RED and self-stamp as trustworthy.
    # The shell tells us in stderr; use it.
    if verdict == "RED" and any(s in (err or "").lower() for s in NOT_FOUND_HINTS):
        verdict = "UNKNOWN"

    note = "" if verdict != "UNKNOWN" else (
        "probe could not evaluate (rc=%s) -- fix the predicate, do not read this "
        "as evidence either way" % rc)
    return {"verdict": verdict, "rc": rc, "secs": round(time.time() - t0, 1),
            "note": note, "tail": tail}


def sweep(lines, state, dry_run=False):
    """Run all runnable verifies; return (results, mutations)."""
    entries = fu_ledger.parse(lines)
    results, mutations = [], []
    t_start = time.time()

    for fu in entries:
        if fu.fu_class and fu.fu_class != "defect":
            continue
        cmd = fu.verify_cmd
        if not cmd:
            continue

        unsafe = fu.unsafe_reason()
        if unsafe:
            results.append({"fu": fu.id, "verdict": "REFUSED", "reason": unsafe,
                            "status": fu.status})
            continue

        if time.time() - t_start > SWEEP_CEILING_S:
            results.append({"fu": fu.id, "verdict": "SKIPPED",
                            "reason": "sweep wall-clock ceiling %ss reached" % SWEEP_CEILING_S,
                            "status": fu.status})
            continue

        r = run_probe(cmd)
        rec = state.setdefault(fu.id, {"greens": 0, "history": []})
        if r["verdict"] == "GREEN":
            rec["greens"] = rec.get("greens", 0) + 1
        elif r["verdict"] == "RED":
            rec["greens"] = 0
        # UNKNOWN leaves the green streak untouched: an unevaluable probe is
        # not evidence in either direction, so it must neither advance nor
        # reset progress toward a close.
        rec["last"] = r["verdict"]
        rec["last_run"] = now()
        rec["history"] = (rec.get("history", []) + [r["verdict"][0]])[-20:]

        row = {"fu": fu.id, "verdict": r["verdict"], "rc": r["rc"], "secs": r["secs"],
               "status": fu.status, "greens": rec["greens"], "tail": r["tail"],
               "note": r["note"], "seen_red": bool(fu.seen_red), "action": "none"}

        # --- observing RED is itself the evidence that the predicate works ----
        # This is what makes the loop self-bootstrapping. A newly written verify
        # starts at `verify_seen_red: NEVER` and is therefore untrusted. The
        # first time it goes RED against the live broken state, it has proved it
        # can fail, so we stamp it and it becomes trusted to close later. No
        # human has to certify a predicate by hand.
        if r["verdict"] == "RED" and not fu.seen_red:
            row["action"] = "stamp-seen-red"
            mutations.append(("stamp", fu.id, now()))

        # --- RED against something we believe is closed => REOPEN -------------
        if r["verdict"] == "RED" and fu.status in CLOSED_STATES:
            row["action"] = "REOPEN"
            mutations.append(("reopen", fu.id,
                              "%s fu-verify: verify went RED against status=%s (rc=%s). "
                              "Reopened automatically -- a closed FU whose predicate fails "
                              "is a regression, not a flake. tail: %s"
                              % (now(), fu.status, r["rc"], r["tail"][:160] or "(no output)")))

        # --- GREEN, gated on having ever been seen RED ------------------------
        elif r["verdict"] == "GREEN" and fu.is_open():
            if not fu.seen_red:
                row["action"] = "green-but-untrusted"
                row["note"] = ("verify has never been observed RED; green recorded but NOT "
                               "acted on -- an assertion never seen fail is not evidence")
            elif rec["greens"] >= GREENS_TO_CLOSE:
                row["action"] = "CLOSE"
                mutations.append(("close", fu.id,
                                  "%s fu-verify: verify GREEN on %d consecutive sweeps "
                                  "(first seen RED %s). Auto-resolved. cmd exited 0 in %ss."
                                  % (now(), rec["greens"], fu.seen_red, r["secs"])))
            else:
                row["action"] = "green-%d-of-%d" % (rec["greens"], GREENS_TO_CLOSE)

        results.append(row)

    return results, mutations


def apply_mutations(lines, mutations):
    """Apply reopen/close edits. Re-parses between each -- indices shift."""
    applied = []
    for kind, fu_id, logtext in mutations:
        entries = {f.id: f for f in fu_ledger.parse(lines)}
        fu = entries.get(fu_id)
        if not fu or "date" not in fu.keys:
            continue

        if kind == "stamp":
            fu_ledger.insert_key(lines, fu, "verify_seen_red", logtext, before="log")
            entries = {f.id: f for f in fu_ledger.parse(lines)}
            fu_ledger.append_log(
                lines, entries[fu_id],
                "%s fu-verify: predicate observed RED against the live system -- it can "
                "fail, so it is now trusted to close this FU when it turns GREEN." % logtext)
            applied.append((kind, fu_id))
            continue

        want = "open" if kind == "reopen" else "resolved"
        dline = lines[fu.keys["date"]]
        if "status:" in dline:
            head, sep, rest = dline.partition("status:")
            tail_parts = rest.split(None, 1)
            newrest = (" %s" % want) + (rest[len(tail_parts[0]) + 1:] if len(tail_parts) > 1
                                        else rest[len(tail_parts[0]):] if tail_parts else "")
            lines[fu.keys["date"]] = head + sep + newrest
        # A reopened FU that still carries its old `- resolution:` text reads as
        # CLOSED to anyone (or anything) grepping for a filled resolution. Demote
        # the stale claim into the log rather than leaving two contradictory
        # signals in one entry.
        if kind == "reopen":
            entries = {f.id: f for f in fu_ledger.parse(lines)}
            fu = entries[fu_id]
            old = (fu.vals.get("resolution") or "").strip()
            if old:
                lines[fu.keys["resolution"]] = "- resolution:"
                entries = {f.id: f for f in fu_ledger.parse(lines)}
                fu_ledger.append_log(
                    lines, entries[fu_id],
                    "%s fu-verify: SUPERSEDED prior resolution (%r) -- it did not hold."
                    % (now(), old[:120]))

        entries = {f.id: f for f in fu_ledger.parse(lines)}
        fu_ledger.append_log(lines, entries[fu_id], logtext)
        applied.append((kind, fu_id))
    return applied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--negative-control", action="store_true",
                    help="prove the runner reports RED and GREEN correctly, then exit")
    args = ap.parse_args()

    if args.negative_control:
        bad = run_probe("exit 3")
        good = run_probe("exit 0")
        slow = run_probe("sleep 5", timeout=1)
        ok = (bad["verdict"] == "RED" and good["verdict"] == "GREEN"
              and slow["verdict"] == "RED")
        out = {"negative_control": {"exit3": bad["verdict"], "exit0": good["verdict"],
                                    "timeout": slow["verdict"]}, "runner_trustworthy": ok}
        print(json.dumps(out, indent=2))
        return 0 if ok else 1

    if not os.path.exists(args.ledger):
        print("FATAL: no ledger at %s" % args.ledger, file=sys.stderr)
        return 2

    with open(args.ledger, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    state = load_state(args.state)
    results, mutations = sweep(lines, state, dry_run=args.dry_run)

    applied = []
    if mutations and not args.dry_run:
        stamp = datetime.now(timezone.utc)
        bdir = os.path.join(BACKUP_ROOT, stamp.strftime("%Y-%m-%d"), "fu-verify")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(args.ledger, os.path.join(
            bdir, "FOLLOWUPS.md.%s.bak" % stamp.strftime("%H%M%SZ")))
        # Probes take minutes; the ledger may well have moved while they ran.
        # Re-read under the lock and re-apply there, so we never write a
        # snapshot that is older than someone else's edit.
        try:
            with fu_lock.ledger_txn(args.ledger) as txn:
                applied = apply_mutations(txn.lines, mutations)
                fu_ledger.parse(txn.lines)   # prove it still parses
        except fu_lock.LedgerChanged as exc:
            print("ABORTED (no verdicts lost, state saved): %s" % exc, file=sys.stderr)
            save_state(args.state, state)
            return 3
        except fu_lock.LedgerBusy as exc:
            print("ABORTED: %s" % exc, file=sys.stderr)
            save_state(args.state, state)
            return 4

    if not args.dry_run:
        save_state(args.state, state)

    summary = {
        "ran": now(),
        "ledger": args.ledger,
        "probed": len(results),
        "green": sum(1 for r in results if r["verdict"] == "GREEN"),
        "red": sum(1 for r in results if r["verdict"] == "RED"),
        "unknown": sum(1 for r in results if r["verdict"] == "UNKNOWN"),
        "refused": sum(1 for r in results if r["verdict"] == "REFUSED"),
        "closed": [f for k, f in applied if k == "close"],
        "reopened": [f for k, f in applied if k == "reopen"],
        "dry_run": args.dry_run,
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("fu-verify %s  probed=%d green=%d red=%d unknown=%d refused=%d%s"
              % (summary["ran"], summary["probed"], summary["green"], summary["red"],
                 summary["unknown"], summary["refused"],
                 "  [DRY RUN]" if args.dry_run else ""))
        for r in results:
            flag = {"CLOSE": "  >> AUTO-RESOLVED", "REOPEN": "  >> AUTO-REOPENED"}.get(
                r.get("action"), "")
            print("  %-9s %-8s %-14s %s%s" % (r["verdict"], r["fu"], r.get("status", ""),
                                              (r.get("note") or r.get("reason") or
                                               r.get("tail", ""))[:90], flag))
        if summary["closed"]:
            print("AUTO-RESOLVED: %s" % ", ".join(summary["closed"]))
        if summary["reopened"]:
            print("AUTO-REOPENED (regression): %s" % ", ".join(summary["reopened"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
