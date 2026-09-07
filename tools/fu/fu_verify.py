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
import re
import shutil
import tempfile
from pathlib import Path
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
    "can't open file",                 # python <missing script>
    # gh could not resolve WHICH repo because the sweep cwd (_tools) is not a
    # checkout and the predicate omitted -R. FU-297 was REOPENED on 10 separate
    # days (2026-08-10..09-05) by exactly this tail, and FU-288 once, each a
    # non-evaluation read as a regression. Added 2026-09-05 (follow-up-triage).
    "not a git repository",
    "failed to determine base repo",
    "error: file or directory not found",   # pytest, given a path that is not there
)

# The INTERPRETER refusing to start a module -- `python.exe: No module named
# pytest` -- also exits 1 and is NOT a finding. It must be told apart from a
# genuine `ModuleNotFoundError: No module named 'app.db'` traceback, which IS
# a finding and is exactly what several predicates are built to detect. The
# interpreter form has no traceback and no quotes around the name.
# BASIS: 2026-07-30 sweep, where this misread REOPENED two correctly-resolved
# entries (FU-187, FU-189) whose predicates named a RUNTIME path that does not
# exist on the tower.
INTERPRETER_NO_MODULE = re.compile(
    r"(?im)^[^\n]*python[^\n]*:\s+No module named\s+[^'\"\n]+$")

# --------------------------------------------------------------------------
# FU-203. Three more cannot-evaluate shapes, every one of them MEASURED in the
# 2026-07-30 17:12Z sweep, where 3 of 5 REDs were probes that never ran:
#
#   FU-202  tail `&& was unexpected at this time.`   -> action=REOPEN
#   FU-027  tail `TimeoutError: The read operation timed out` -> stamp-seen-red
#   FU-124  tail `HTTPError: HTTP Error 404: Not Found` (uncaught)
#
# FU-202 is the one that shows the cost. Its predicate is
# `cd D:\zo\_lanes\<lane> && python tools/runbook_pin.py` -- `<lane>` is a
# PLACEHOLDER the author never substituted, so cmd.exe rejected the line before
# running anything, returned 1, and the sweep was one non-dry-run away from
# REOPENING an FU that prod-drift-sentinel had resolved with a merged PR and a
# mutation control four hours earlier. A shell's refusal to parse is not a
# finding about the system.
#
# FU-027 is worse in kind. rc=1 from a network timeout was scheduled to
# `stamp-seen-red` -- writing the marker that makes an UNTRUSTED predicate
# TRUSTED. That launders a non-observation into the trust token this whole
# mechanism rests on: the predicate would then be believed the next time it
# went green, having never once been observed failing for a real reason.
# --------------------------------------------------------------------------

# The shell itself refused to parse the command. cmd.exe writes this to STDOUT,
# which is why inspecting stderr alone missed it.
SHELL_SYNTAX_HINTS = (
    "was unexpected at this time",              # cmd.exe: stray && / ( / )
    "syntax error near unexpected token",       # sh/bash
    "unexpected token",                         # PowerShell parser
    "the syntax of the command is incorrect",
    "missing argument in parameter list",
)

# `<lane>`, `<name>`, `<NNN>` -- an unsubstituted template slot. Caught
# STATICALLY, before execution, because the failure mode is not always a shell
# error: `cd D:\path\<lane>` on some shells simply fails silently at rc=1 and
# reads as a clean RED. Deliberately narrow: requires a bare identifier in
# angle brackets with no whitespace, so `<` as redirection (`cmd < file`),
# SQL `<>`, and `<=` / `>=` comparisons are all untouched.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][A-Za-z0-9_.-]*>")

# An uncaught traceback ending in an ENVIRONMENTAL failure. The probe crashed
# on the network or the filesystem; it did not evaluate its assertion.
# Deliberately NOT ModuleNotFoundError/ImportError/AssertionError -- those are
# findings several predicates exist to detect.
ENV_CRASH_RE = re.compile(
    r"(?m)^\s*(?:socket\.|ssl\.|urllib\.error\.|requests\.exceptions\.|http\.client\.)?"
    r"(TimeoutError|ConnectionError|ConnectionRefusedError|ConnectionResetError|"
    r"ConnectionAbortedError|URLError|HTTPError|SSLError|SSLEOFError|RemoteDisconnected|"
    r"IncompleteRead|gaierror|timeout)\b")

# A pytest failure report also contains tracebacks, and IS a finding. Never
# reclassify one as unevaluable.
PYTEST_REPORT_HINTS = ("short test summary info", "=== FAILURES ===", "= FAILURES =",
                       " failed, ", " failed in ", "assert ")


def _looks_like_a_real_test_failure(blob: str) -> bool:
    low = (blob or "").lower()
    return any(h.lower() in low for h in PYTEST_REPORT_HINTS)


def placeholder_in(cmd: str):
    """Return the first unsubstituted `<placeholder>` in `cmd`, else None."""
    m = PLACEHOLDER_RE.search(cmd or "")
    return m.group(0) if m else None


def _is_unevaluable(err: str, out: str = "") -> bool:
    """True when the OUTPUT says the probe could not RUN, not that it failed.

    Reads BOTH streams: cmd.exe reports a syntax refusal on stdout, so a
    stderr-only check silently missed FU-202 (see FU-203).
    """
    blob = "%s\n%s" % (err or "", out or "")
    low = blob.lower()
    if any(s in low for s in NOT_FOUND_HINTS):
        return True
    if any(s in low for s in SHELL_SYNTAX_HINTS):
        return True
    if "modulenotfounderror" in low:
        return False        # a real traceback: the module under test is missing
    if ("traceback (most recent call last)" in low
            and ENV_CRASH_RE.search(blob)
            and not _looks_like_a_real_test_failure(blob)):
        return True         # crashed on the network/filesystem; never evaluated
    return bool(INTERPRETER_NO_MODULE.search(blob))


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


# --------------------------------------------------------------------------
# FU-221. THE HARNESS'S OWN EXECUTION CONTRACT WAS UNDECLARED, AND 14 OF ITS 29
# UNKNOWNS WERE THAT, NOT BROKEN PREDICATES.
#
# MEASURED, 2026-08-01T17:12Z sweep: 76 probed, 41 GREEN, 5 RED, 29 UNKNOWN,
# 1 REFUSED -- 38% blindness, up from 27 the run before, which had already named
# it "the next run's first target". Reading the 29 tails instead of the count
# splits them cleanly:
#
#   CWD    (~10) the predicate is written relative to the repo root or the
#          workspace root; the sweep runs with cwd=_tools. FU-219's
#          `python tools/fire_gate.py` becomes `_tools\tools\fire_gate.py`,
#          FU-218's `python _tools\kl_link_audit.py` becomes `_tools\_tools\...`,
#          and four `pytest tests/test_*.py` predicates find no tests at all.
#   SHELL  (~4)  the predicate is PowerShell or POSIX and shell=True on Windows
#          is cmd.exe. FU-215's `Select-String` -- authored THIS MORNING with a
#          measured both-directions control -- came back
#          `'Select-String' is not recognized`. FU-188 uses `pgrep`; FU-159 and
#          FU-162 use `test`.
#   REAL   (~15) bus unreachable, HTTP 404, rc=6. Genuinely cannot evaluate here.
#
# This is HARNESS_DOCTRINE class 1 aimed at the acceptance harness itself: the
# artifact the predicate's author inspected (their command, in PowerShell, at
# the repo root) is not the artifact that runs (cmd.exe, at _tools). Nothing
# stated the contract, so every author had to guess it, and a third of the
# ledger's acceptance checks were blind without anyone choosing that.
#
# THE FIX IS RECOVERY, NOT RESTRICTION (R7), AND IT IS ASYMMETRIC BY DESIGN:
#
#   * Attempt 1 is EXACTLY what ran before -- same cwd, same shell. If it
#     returns GREEN or RED, that verdict is returned unchanged and no recovery
#     is attempted. A verdict this harness can already reach can never be
#     altered by this code. That is what makes the change non-regressive: the
#     41 GREENs and 5 REDs of the sweep above are untouchable by it.
#   * Recovery runs ONLY on UNKNOWN, and only on the narrow subset whose output
#     says the environment could not find the file or the command. A network
#     UNKNOWN is not retried -- a different cwd cannot reach a down bus, and
#     retrying 15 of those would cost more wall clock than the sweep has.
#   * A recovered verdict NAMES the root that answered (`resolved_by`) and
#     carries the exact prefix that would make the predicate self-sufficient.
#     Silent recovery would be the FU-218 error one level up: a widening that
#     cannot be told apart from a silencing.
#
# DELIBERATELY CWD-ONLY, NOT SHELL. Re-running a predicate under PowerShell
# would fix the four SHELL cases, but `powershell -Command <native cmd>` does
# not reliably propagate the child's exit code, and this harness's ENTIRE
# meaning is the 0/1/>=2 contract. Trading verdict integrity for four
# recoveries is the wrong trade. Those four are diagnosed by name instead
# (see SHELL_ONLY_TOKENS) so the ledger's sole writer can repair the predicates
# themselves, which is a better outcome than a harness that tolerates them.
# --------------------------------------------------------------------------

_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ordered candidate working directories for RECOVERY only. The first entry is
# never used for recovery (it is attempt 1's own cwd, already tried).
RECOVERY_ROOTS = [
    ("workspace", _WORKSPACE_ROOT),                        # D:\zo\Zocomputer Agents
    ("repo", os.path.join(os.path.dirname(_WORKSPACE_ROOT), "zo-sentinel", "zo-sentinel")),
    ("zo", os.path.dirname(_WORKSPACE_ROOT)),              # D:\zo
]

# Commands that exist in PowerShell or POSIX but NOT in cmd.exe. A predicate
# whose only problem is one of these is not fixed by any cwd; say so precisely
# rather than reporting a generic "could not evaluate".
SHELL_ONLY_TOKENS = (
    "select-string", "test-path", "get-content", "get-childitem", "get-item",
    "measure-object", "where-object", "select-object", "compare-object",
    "out-string", "convertfrom-json", "resolve-path",
    "pgrep", "pkill", "grep", "awk", "sed", "wc", "head", "tail", "test", "[",
)

_NOT_RECOGNISED_RE = re.compile(
    r"'([^']+)' is not recognized as an internal or external command", re.I)


def _environment_miss(out: str, err: str):
    """Classify an UNKNOWN as a *locatable* miss, or None if it is not one.

    Returns "path" (a file/dir the shell could not find -- retrying at another
    cwd is meaningful) or "shell:<token>" (a command that does not exist in
    cmd.exe at any cwd -- retrying is pointless and the note must say why).

    NARROWER THAN _is_unevaluable ON PURPOSE. That function answers "should
    this count as evidence?" and correctly says no to network crashes too.
    This one answers "is there another place this could have run?", and a
    down bus is not somewhere else.
    """
    blob = "%s\n%s" % (err or "", out or "")
    low = blob.lower()

    m = _NOT_RECOGNISED_RE.search(blob)
    if m:
        tok = m.group(1).strip().lower()
        if tok in SHELL_ONLY_TOKENS:
            return "shell:%s" % tok
        return "path"
    if "the term '" in low:                      # PowerShell's own phrasing
        return "path"
    for hint in ("can't open file", "no such file or directory",
                 "cannot find the path", "cannot find the file specified",
                 "error: file or directory not found", "no tests ran"):
        if hint in low:
            return "path"
    return None


_BARE_SCRIPT_RE = re.compile(
    r'^\s*(?:"([^"]+\.py)"|\'([^\']+\.py)\'|(\S+\.py))(?=\s|$)')


def _interpreterise(cmd: str) -> tuple[str, str | None]:
    """FU-293. A predicate written as a BARE `.py` path OPENS NOTEPAD.

    -> (command_to_run, note_if_rewritten)

    MEASURED, NOT REASONED. On 2026-08-08 the detached sweep sat for 25 minutes at
    near-zero CPU. Its child was `cmd.exe /c "rule_echo.py --self-test"`, and that
    cmd.exe's child was:

        Notepad.exe "D:\\zo\\Zocomputer Agents\\_tools\\rule_echo.py"

    `.py` is associated with `py.exe`, but a bare script path passed through
    `shell=True` on this host resolves via the shell's OPEN verb, and the OPEN verb
    here launches the editor. So the predicate does not run. It opens its own
    source file in a GUI text editor, on a HEADLESS SCHEDULED RUN, and the runner
    blocks on it until CMD_TIMEOUT_S. A Notepad window holding `fu_verify.py` from
    2026-08-07T13:18 was still alive 28 hours later, orphaned by exactly this --
    `subprocess.run`'s timeout kills the cmd.exe, never the grandchild it spawned.

    THE COST WAS PAID THREE TIMES OVER AND NONE OF IT WAS VISIBLE. (1) Every such
    predicate has NEVER ONCE EXECUTED -- it has only ever been recorded UNKNOWN,
    and UNKNOWN is where a check goes to be politely ignored. (2) 60 wasted seconds
    each turned a "~72s" sweep (as every task prompt still describes it) into 25+
    minutes, which is the whole reason it must be run detached. (3) It leaks a GUI
    process per predicate per run onto a machine nobody is sitting at.

    THE HONEST READING OF THE OLD BEHAVIOUR IS NOT "SAFE". It is R4 in its purest
    form: an assertion never once observed running was carried in the ledger beside
    assertions that were, in the same register, and nothing distinguished them.

    R7 -- RECOVERY OVER RESTRICTION. The tempting fix is to REFUSE bare-.py
    predicates and make their authors rewrite them. That is a new gate, and gate-
    stacking produced 51% of this ledger. Instead the runner supplies the
    interpreter it is itself running under, which is what the author meant, and
    SAYS SO in the note so the rewrite is never silent. Narrow by construction: it
    fires only when the FIRST token is a `.py` path and nothing precedes it, so any
    command that already names an interpreter, or pipes, or sets a variable first,
    is untouched.
    """
    m = _BARE_SCRIPT_RE.match(cmd)
    if not m:
        return cmd, None
    script = m.group(1) or m.group(2) or m.group(3)
    rest = cmd[m.end():]
    new = f'"{sys.executable}" "{script}"{rest}'
    return new, (f"FU-293: bare '.py' predicate rewritten to run under "
                 f"{os.path.basename(sys.executable)} -- as written it would have "
                 f"opened an editor, not executed")


def _probe_once(cmd: str, timeout: int, cwd=None) -> dict:
    """One execution + classification. No recovery, no policy."""
    t0 = time.time()
    cmd, _rewrite_note = _interpreterise(cmd)
    # FU-303 (follow-up-triage, 2026-08-10). DO NOT restore capture_output=True here.
    # MEASURED, both poles, `_staging/fu303_mechanism_control.py`:
    #     pipe form      -> returned after 45.1s on a 4s timeout   (11.3x overrun)
    #     file-sink form -> returned after  4.2s on a 4s timeout
    # subprocess.run's timeout kills only cmd.exe and then drains the pipes; a
    # GRANDCHILD that outlives it still holds the inherited write handle, so the
    # pipe never sees EOF and the "timeout" blocks until the grandchild exits.
    # This function's own sibling docstring (_interpreterise, ~line 327) already
    # said "subprocess.run's timeout kills the cmd.exe, never the grandchild it
    # spawned" -- and this call kept the pipes anyway, so BOTH advertised bounds
    # were fiction: CMD_TIMEOUT_S could not fire, and SWEEP_CEILING_S is only
    # consulted BETWEEN probes so it could not fire either. Cost: `fu_verify --once`
    # hung 28 min on 2026-08-09 (FU-303) and 23+ min on 2026-08-10, and
    # fu_verify_state.json was last written 2026-08-08 -- three days in which every
    # auto-close and auto-reopen in this ledger was dark while the ledger looked fine.
    # friction.py already names this family `capture-output-shell-hang`.
    _fd, _sink_path = tempfile.mkstemp(prefix="fuverify_", suffix=".txt")
    os.close(_fd)
    _timed_out = False
    try:
        try:
            with open(_sink_path, "wb") as _sink:
                p = subprocess.Popen(cmd, shell=True, stdout=_sink, stderr=_sink,
                                     stdin=subprocess.DEVNULL, cwd=cwd)
                try:
                    rc = p.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _timed_out = True
                    # /T because the direct child is cmd.exe and the thing that
                    # actually overruns is always its descendant.
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
                    try:
                        rc = p.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        rc = None
        except OSError as exc:
            return {"verdict": "UNKNOWN", "rc": None, "secs": round(time.time() - t0, 1),
                    "note": "could not execute: %s" % exc, "tail": "", "out": "", "err": ""}

        _blob = Path(_sink_path).read_text(encoding="utf-8", errors="replace")
    finally:
        try:
            os.unlink(_sink_path)
        except OSError:
            pass

    if _timed_out:
        return {"verdict": "UNKNOWN", "rc": None, "secs": round(time.time() - t0, 1),
                "note": "TIMEOUT after %ss -- probe could not answer "
                        "(process tree killed)" % timeout,
                "tail": _blob.strip().replace("\n", " | ")[-240:],
                "out": _blob, "err": ""}

    # The sink merges the two streams, which is what `tail` concatenated anyway.
    # `err` is kept as the same text so _is_unevaluable() still sees shell errors.
    out, err = _blob, _blob

    tail = ((out + err).strip().replace("\n", " | "))[-240:]
    verdict = "GREEN" if rc == 0 else ("RED" if rc == 1 else "UNKNOWN")

    # cmd.exe returns rc=1 for a command it cannot find, which is
    # indistinguishable from "the predicate ran and the bug is present". A
    # typo'd probe would therefore read as RED and self-stamp as trustworthy.
    # The shell tells us in stderr; use it.
    if verdict == "RED" and _is_unevaluable(err, out):
        verdict = "UNKNOWN"

    note = "" if verdict != "UNKNOWN" else (
        "probe could not evaluate (rc=%s) -- fix the predicate, do not read this "
        "as evidence either way" % rc)
    if _rewrite_note:
        note = (note + " | " if note else "") + _rewrite_note
    return {"verdict": verdict, "rc": rc, "secs": round(time.time() - t0, 1),
            "note": note, "tail": tail, "out": out, "err": err}


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
    # FU-203: refuse to run a predicate carrying an unsubstituted template slot.
    # Checked BEFORE execution because the shell's reaction to `<lane>` varies --
    # cmd.exe rejects the line, sh may just fail the `cd` at rc=1, which is
    # indistinguishable from a clean RED.
    ph = placeholder_in(cmd)
    if ph:
        return {"verdict": "UNKNOWN", "rc": None, "secs": 0.0,
                "note": "predicate carries an unsubstituted placeholder %s -- it was "
                        "NEVER RUN. Substitute it or rewrite the verify; this is a "
                        "sentence about a probe, not a probe." % ph,
                "tail": ""}

    # ---- attempt 1: byte-for-byte the pre-FU-221 behaviour -----------------
    first = _probe_once(cmd, timeout)
    out, err = first.pop("out", ""), first.pop("err", "")

    # A verdict this harness could already reach is FINAL. Recovery can never
    # touch a GREEN or a RED, which is the whole safety argument for it.
    if first["verdict"] != "UNKNOWN":
        return first

    kind = _environment_miss(out, err)
    if kind is None:
        first["tried"] = ["cwd=%s (default)" % os.getcwd()]
        return first

    if kind.startswith("shell:"):
        tok = kind.split(":", 1)[1]
        first["note"] = (
            "probe could not evaluate: %r does not exist in cmd.exe, which is "
            "the shell this harness runs (shell=True on Windows). NO cwd fixes "
            "this -- the predicate itself must be rewritten to something cmd.exe "
            "can run, e.g. wrap it as "
            "`powershell -NoProfile -Command \"...; exit $LASTEXITCODE\"` or "
            "replace it with a python one-liner that exits 0/1. Not evidence in "
            "either direction." % tok)
        first["resolved_by"] = None
        first["tried"] = ["cwd=%s (default)" % os.getcwd()]
        return first

    # ---- recovery: same command, same shell, other declared roots ----------
    rec_timeout = min(timeout, 30)
    tried = ["cwd=%s (default)" % os.getcwd()]
    here = os.path.normcase(os.path.abspath(os.getcwd()))
    for label, root in RECOVERY_ROOTS:
        if not os.path.isdir(root) or os.path.normcase(os.path.abspath(root)) == here:
            continue
        tried.append("cwd=%s [%s]" % (root, label))
        r = _probe_once(cmd, rec_timeout, cwd=root)
        r.pop("out", None)
        r.pop("err", None)
        if r["verdict"] == "UNKNOWN":
            continue
        r["resolved_by"] = "%s (%s)" % (label, root)
        r["tried"] = tried
        r["recovered"] = True
        r["note"] = (
            "RECOVERED at cwd=%s [%s] -- the verdict below is real, but the "
            "PREDICATE IS UNDER-SPECIFIED: it assumes a working directory it "
            "does not state, and the sweep runs from %s. Repair it by prefixing "
            "`cd /d \"%s\" && ` or by using absolute paths, so it stops depending "
            "on who invoked it." % (root, label, os.getcwd(), root))
        return r

    first["tried"] = tried
    first["resolved_by"] = None
    first["note"] += (
        " | FU-221 recovery tried %d root(s) and none could run it either: %s"
        % (len(tried) - 1, "; ".join(tried[1:])))
    return first


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

        # FU-221 self-correction, caught by the first dry-run AFTER the fix
        # shipped. This row was built from a fixed key list, so `recovered`,
        # `resolved_by` and `tried` were DROPPED here -- the sweep reported
        # `RECOVERED: 0` while four UNKNOWNs had in fact become verdicts at
        # another root. The whole safety argument for recovery is that it
        # NAMES where it ran; a provenance field that never reaches the report
        # is provenance nobody has. That is the exact defect class this run
        # spent the day on, committed inside the fix for it, and it was found
        # only by reading the fix's own output instead of trusting it.
        for k in ("recovered", "resolved_by", "tried"):
            if k in r:
                row[k] = r[k]

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
        entries = fu_ledger.by_id(fu_ledger.parse(lines))
        fu = entries.get(fu_id)
        if not fu or "date" not in fu.keys:
            continue

        if kind == "stamp":
            fu_ledger.insert_key(lines, fu, "verify_seen_red", logtext, before="log")
            entries = fu_ledger.by_id(fu_ledger.parse(lines))
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
            entries = fu_ledger.by_id(fu_ledger.parse(lines))
            fu = entries[fu_id]
            old = (fu.vals.get("resolution") or "").strip()
            if old:
                lines[fu.keys["resolution"]] = "- resolution:"
                entries = fu_ledger.by_id(fu_ledger.parse(lines))
                fu_ledger.append_log(
                    lines, entries[fu_id],
                    "%s fu-verify: SUPERSEDED prior resolution (%r) -- it did not hold."
                    % (now(), old[:120]))

        entries = fu_ledger.by_id(fu_ledger.parse(lines))
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
        # FU-222: THIS CONTROL ASSERTED THE OPPOSITE OF THE CONTRACT THIS FILE
        # DOCUMENTS, AND HAD THEREFORE ANSWERED "no" EVERY TIME IT WAS EVER RUN.
        #
        # `--negative-control` is the one command whose job is to answer: can I
        # believe this runner? It asserted `run_probe("exit 3") == "RED"` and
        # `timeout == "RED"`. But run_probe's own docstring, forty lines up,
        # states the three-state contract that is the entire point of this
        # harness: rc>=2 is UNKNOWN, and a TIMEOUT is UNKNOWN. So both arms
        # asserted a verdict the code is designed never to produce, and
        # `runner_trustworthy` was structurally incapable of being true.
        # MEASURED both against the current file and against the pre-FU-221
        # backup: identical `{exit3: UNKNOWN, exit0: GREEN, timeout: UNKNOWN,
        # runner_trustworthy: false}`. Pre-existing, not a regression.
        #
        # THE COST IS NOT THE WRONG BOOLEAN, IT IS THAT rc=1 WAS THE STEADY
        # STATE. `a_gate_that_is_permanently_red`, aimed at the trust-check of
        # the acceptance harness itself: a control that always says the runner
        # is untrustworthy cannot report the day the runner ACTUALLY breaks,
        # because no reader can separate the new red from the standing one.
        # Same shape as FU-218's link auditor, one level further in -- this is
        # the instrument that grades the instruments.
        #
        # TWO DEFECTS, NOT ONE. `sleep` is not a cmd.exe command, so the
        # "timeout" arm never timed out; it failed at not-found and was
        # reclassified UNKNOWN by the not-found path. It has never once
        # exercised subprocess.TimeoutExpired. And there was NO rc=1 case at
        # all -- the runner's ability to report RED, the verdict that REOPENS
        # closed FUs, was never controlled for by the control.
        #
        # Rewritten to assert the documented contract, with an arm per state
        # and a timeout that really times out.
        cases = [
            ("exit0_is_green", run_probe("exit 0"), "GREEN"),
            ("exit1_is_red", run_probe("exit 1"), "RED"),
            ("exit3_is_unknown", run_probe("exit 3"), "UNKNOWN"),
            ("timeout_is_unknown",
             run_probe('python -c "import time; time.sleep(20)"', timeout=3),
             "UNKNOWN"),
            ("missing_command_is_unknown_not_red",
             run_probe("no_such_binary_fu222_xyz"), "UNKNOWN"),
        ]
        observed = {name: r["verdict"] for name, r, _ in cases}
        expected = {name: want for name, _, want in cases}
        mismatched = {n: {"want": expected[n], "got": observed[n]}
                      for n in expected if observed[n] != expected[n]}
        ok = not mismatched
        out = {"negative_control": observed,
               "expected": expected,
               "mismatched": mismatched,
               "contract": "0=GREEN 1=RED >=2/timeout/not-found=UNKNOWN",
               "runner_trustworthy": ok}
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
