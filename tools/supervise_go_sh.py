#!/usr/bin/env python3
"""
supervise_go_sh.py -- host patcher: go.sh relaunches a daemon that is RUNNING
BUT UNSUPERVISED instead of reading it as "already running".
(RCA 2026-10 follow-up #1; sibling of harden_go_sh.py.)

THE FAULT (2026-10-01, 5 orphans re-parented by hand)
  Section 1 of /home/workspace/zo_mesh/go.sh does `pkill -f 'daemon_wrapper.sh'`
  -- every wrapper dies -- but kills only SOME daemons by name. The rest
  (ladder_shim, ui_server, watchdog_daemon, registration_drift_check,
  autopoiesis_bar_tracker in the v2.9.2 snapshot) survive with their parent gone:
  alive, serving their port, supervised by nobody. Then:
    * ladder_shim / ui_server: `curl :port/health == 200` -> "already running" -> skip
    * watchdog_daemon: `pgrep -f watchdog_daemon.py` -> found -> skip
    * registration_drift_check / autopoiesis_bar_tracker: launched again
      unconditionally -> a supervised copy BESIDE the orphan
  so every `zm go` can manufacture orphans and never repairs one. If such a
  daemon exits, nothing respawns it (daemon_supervision.py: ORPHAN).

THE PATCH (idempotent; a missing anchor changes NOTHING)
  1. A helper `zo_reap_unsupervised <name> <script>`: kill every python process
     running <script> whose parent is NOT a live `daemon_wrapper.sh <name>`
     (TERM, then KILL after 3s). A supervised instance is never touched.
     Supervision is judged by the PARENT-PID LINK, never by a name census --
     a zombie wrapper has no cmdline (daemon_supervision.py's rule).
  2. A generated block, right AFTER section 1's wrapper purge, calling the
     helper once for every literal `daemon_wrapper.sh <name> <script.py>` that
     go.sh itself declares. At that point no wrapper is alive, so anything still
     running is unsupervised by definition; it is killed there, and go.sh's own
     checks further down then fall through and relaunch it under its wrapper.
     The roster is regenerated between markers on every run, so a daemon added
     to go.sh later (or present only on the live host) is covered next run.
  Pre-existing orphans (e.g. from a reload_daemon.sh reload, FU-460) are caught
  too: the test is the parent link, not "was its wrapper just killed".

WHY EVERY LINE IS GUARDED
  go.sh runs under `set -e` + `set -E` with an ERR trap that does
  `exec sleep infinity`. One failing command in the helper -- or a call to a
  helper that was never inserted -- wedges the boot. So the helper ends in
  `return 0`, every command carries `|| true` or sits in a condition, there is
  no `((n++))` (returns 1 at 0), and calls are only inserted when the helper is.
  --self-test RUNS the helper under exactly that trap against real processes.

Usage (on ZoComputer):
    python3 tools/supervise_go_sh.py              # patch in place (.supbak written)
    python3 tools/supervise_go_sh.py --dry-run    # report only
    python3 tools/supervise_go_sh.py --self-test  # two-pole, text + live processes
    python3 tools/supervise_go_sh.py --file /path/to/go.sh
Re-run after every REFRESH_MODE=reset (it reverts host patches), with
harden_go_sh.py. Exit: 0 applied/no-op, 2 file missing or anchor not found.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/go.sh"
HELPER_SEEN = "zo_reap_unsupervised() {"
BEGIN = "# >>> supervise_go_sh: reap unsupervised daemons (generated -- do not edit) >>>"
END = "# <<< supervise_go_sh <<<"
# The purge line the block must follow, and the section the helper precedes.
PURGE = re.compile(r"^pkill -f ['\"]daemon_wrapper\.sh['\"][^\n]*\n", re.M)
HELPER_ANCHOR = re.compile(r"^trap '_go_err_trap' ERR\n", re.M)

HELPER = r'''# --- supervision-aware relaunch (RCA 2026-10 follow-up #1, supervise_go_sh.py) ---
# Kill every python process running <script> whose parent is NOT a live
# `daemon_wrapper.sh <name>`, so go.sh's "already running?" check below falls
# through and relaunches it UNDER its wrapper. A supervised instance is never
# touched. Every command is guarded: this file runs under set -e + an ERR trap
# that wedges the boot on any failing step, so this function must never fail.
zo_reap_unsupervised() {
  local name="$1" script="$2" pid ppid pcmd pargs pstat victims="" n=0
  for pid in $(pgrep -f -- "$script" 2>/dev/null || true); do
    [[ "$pid" == "$$" ]] && continue
    pcmd=$(ps -o args= -p "$pid" 2>/dev/null || true)
    # the DAEMON only: python whose program argument IS the script -- not the wrapper
    # (it carries the path as an argument too), not a tool that merely names it.
    [[ "$pcmd" =~ (^|/)python[0-9.]*(\ +-[A-Za-z]+)*\ +"$script"(\ |$) ]] || continue
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
    pstat=$(ps -o stat= -p "${ppid:-0}" 2>/dev/null | tr -d ' ' || true)
    pargs=$(ps -o args= -p "${ppid:-0}" 2>/dev/null || true)
    if [[ -n "$ppid" && "$ppid" != "1" && "$pstat" != Z* \
          && " $pargs " == *"daemon_wrapper.sh $name "* ]]; then
      continue                                          # supervised: leave it alone
    fi
    victims="$victims $pid"; n=$((n + 1))
    kill "$pid" 2>/dev/null || true
  done
  if [[ "$n" -gt 0 ]]; then
    sleep 3
    for pid in $victims; do kill -0 "$pid" 2>/dev/null && { kill -9 "$pid" 2>/dev/null || true; }; done
    warn "$name: killed $n UNSUPERVISED instance(s) (pid$victims) -- go.sh relaunches it under daemon_wrapper.sh"
  fi
  return 0
}

'''


def declared(src: str):
    """Literal wrapper declarations in go.sh -> [(name, script)], first wins.
    Same rule as daemon_supervision.declared_roster: a bare identifier followed
    by a .py path. Loop forms (`"$NAME" "$SENTINEL/$sc"`) are skipped -- those
    daemons are killed by name in section 1 already."""
    out, seen = [], set()
    toks = src.replace("\\\n", " ").split()
    for i, t in enumerate(toks):
        if t.endswith("daemon_wrapper.sh") and i + 2 < len(toks):
            name, script = toks[i + 1], toks[i + 2]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            if not re.fullmatch(r"[\w$/{}.-]+\.py", script) or "$sc" in script or "$NAME" in script:
                continue
            if name not in seen:
                seen.add(name)
                out.append((name, script))
    return out


def render_block(roster) -> str:
    lines = [BEGIN,
             "# Section 1 just killed every daemon_wrapper.sh; a daemon below that is still",
             "# running is supervised by nobody. Reap it so the checks further down relaunch it.",
             "# Regenerated from this file's own daemon_wrapper.sh declarations on every run."]
    lines += ["zo_reap_unsupervised %s %s" % (n, s) for n, s in roster]
    return "\n".join(lines + [END]) + "\n"


def patch_text(src: str):
    """-> (out, applied, problems). Pure. Never inserts a call without the helper."""
    applied, problems = [], []
    out = src
    roster = declared(src)
    if not roster:
        return src, [], ["no literal daemon_wrapper.sh declarations found -- not a go.sh?"]
    has_helper = HELPER_SEEN in out
    if not has_helper and not HELPER_ANCHOR.search(out):
        return src, [], ["ERR-trap anchor `trap '_go_err_trap' ERR` not found -- version drift; NOTHING changed"]
    if BEGIN not in out and not PURGE.search(out):
        return src, [], ["section-1 purge `pkill -f 'daemon_wrapper.sh'` not found -- NOTHING changed"]
    if not has_helper:
        m = HELPER_ANCHOR.search(out)
        out = out[:m.end()] + "\n" + HELPER + out[m.end():]
        applied.append("helper zo_reap_unsupervised")
    block = render_block(roster)
    if BEGIN in out:
        i = out.index(BEGIN)
        j = out.find(END, i)
        if j < 0:
            return src, [], ["block BEGIN marker without END -- hand-edited? NOTHING changed"]
        j = out.index("\n", j) + 1 if "\n" in out[j:] else len(out)
        if out[i:j] != block:
            out = out[:i] + block + out[j:]
            applied.append("reap block regenerated (%d daemons)" % len(roster))
    else:
        m = PURGE.search(out)
        out = out[:m.end()] + block + out[m.end():]
        applied.append("reap block (%d daemons)" % len(roster))
    return out, applied, problems


# ------------------------------------------------------------------ self-test
SNAPSHOT = Path(__file__).resolve().parent.parent / "ops" / "host" / "go.sh"


def _bash_live_test(checks):
    """Run the inserted helper for real: one supervised daemon, one orphan, under
    go.sh's own `set -eE` + ERR trap. RED = the old check reads the orphan as
    'already running'; GREEN = the helper kills only the orphan and never trips."""
    if not shutil.which("bash") or not shutil.which("pgrep") or not shutil.which("python3"):
        print("  skip  live-process test (needs bash, pgrep, python3)")
        return
    d = Path(tempfile.mkdtemp(prefix="supgo_"))
    try:
        for n in ("ladder_shim", "ui_server"):
            (d / ("%s.py" % n)).write_text("import time\nwhile True: time.sleep(1)\n")
        (d / "daemon_wrapper.sh").write_text(
            '#!/bin/bash\nwhile true; do python3 "$2"; sleep 1; done\n')
        wrap = subprocess.Popen(["bash", str(d / "daemon_wrapper.sh"), "ladder_shim",
                                 str(d / "ladder_shim.py")], start_new_session=True)
        # an ORPHAN: started by a shell that then exits, so its parent is init / a subreaper
        subprocess.run(["bash", "-c", "nohup python3 %s >/dev/null 2>&1 &" % (d / "ui_server.py")])
        # a BYSTANDER: unsupervised python that merely names the path (an editor, a probe)
        subprocess.run(["bash", "-c", "nohup python3 -c 'import time; time.sleep(60)' %s >/dev/null 2>&1 &"
                        % (d / "ui_server.py")])
        time.sleep(1.5)

        def pids(script):
            p = subprocess.run(["pgrep", "-f", "python3 %s" % script], capture_output=True, text=True)
            return [int(x) for x in p.stdout.split()]

        def bystanders():
            p = subprocess.run(["pgrep", "-f", "time.sleep.60. %s" % (d / "ui_server.py")],
                               capture_output=True, text=True)
            return [int(x) for x in p.stdout.split()]
        by = bystanders()
        sup, orph = pids(d / "ladder_shim.py"), pids(d / "ui_server.py")
        # go.sh's own check shape, run from a FILE as go.sh runs it: on a `bash -c`
        # command line the pattern would match its own shell (the c131 self-match).
        (d / "old_check.sh").write_text('pgrep -f "python.*%s" >/dev/null && echo SKIP || echo LAUNCH\n'
                                        % (d / "ui_server.py"))
        old_check = ["bash", str(d / "old_check.sh")]
        red = subprocess.run(old_check, capture_output=True, text=True).stdout.strip()
        checks.append(("RED: the old check reads the orphan as 'already running' (SKIP)",
                       bool(orph) and red == "SKIP"))
        script = ("set -eE\nwarn(){ echo \"WARN $*\"; }\n"
                  "trap 'echo TRAPPED; exit 99' ERR\n" + HELPER +
                  "zo_reap_unsupervised ladder_shim %s/ladder_shim.py\n"
                  "zo_reap_unsupervised ui_server %s/ui_server.py\n"
                  "zo_reap_unsupervised not_running %s/nothing_here.py\n"
                  "echo DONE\n" % (d, d, d))
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        time.sleep(0.5)
        checks.append(("GREEN: helper completes under set -eE + ERR trap (never wedges boot)",
                       p.returncode == 0 and "DONE" in p.stdout and "TRAPPED" not in p.stdout))
        checks.append(("GREEN: the orphan is killed and named",
                       not pids(d / "ui_server.py") and "ui_server: killed 1 UNSUPERVISED" in p.stdout))
        checks.append(("GREEN: a python process that merely NAMES the script is not killed",
                       bool(by) and bystanders() == by))
        checks.append(("GREEN: the supervised daemon is untouched (same pid)",
                       bool(sup) and pids(d / "ladder_shim.py") == sup))
        for b in bystanders():   # it also fools go.sh's own loose pgrep; it has done its job
            os.kill(b, 9)
        time.sleep(0.3)
        red2 = subprocess.run(old_check, capture_output=True, text=True).stdout.strip()
        checks.append(("GREEN: the old check now falls through to LAUNCH", red2 == "LAUNCH"))
    finally:
        try:
            os.killpg(wrap.pid, 9)
        except Exception:  # noqa: BLE001
            pass
        subprocess.run(["pkill", "-9", "-f", str(d)], capture_output=True)
        shutil.rmtree(d, ignore_errors=True)


def self_test() -> int:
    checks = []
    src = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else None
    if src is None:
        print("  FAIL  fixture ops/host/go.sh missing")
        return 1
    out, applied, problems = patch_text(src)
    names = [n for n, _ in declared(src)]
    five = ["ladder_shim", "ui_server", "watchdog_daemon", "registration_drift_check",
            "autopoiesis_bar_tracker"]
    checks.append(("the 5 orphans of 2026-10-01 are all reaped",
                   all(("zo_reap_unsupervised %s " % n) in out for n in five)))
    checks.append(("loop forms ($NAME/$sc) are not mis-parsed into the roster",
                   not any("$" in n for n in names) and "zo_reap_unsupervised \"$NAME\"" not in out))
    checks.append(("helper defined BEFORE the block that calls it, block right after the purge",
                   out.index(HELPER_SEEN) < out.index(BEGIN)
                   and out.index(BEGIN) == PURGE.search(out).end()))
    out2, applied2, _ = patch_text(out)
    checks.append(("idempotent: a re-run is a byte-for-byte no-op", out2 == out and not applied2))
    grown = out.replace("nohup bash $MESH/daemon_wrapper.sh ui_server",
                        "nohup bash $MESH/daemon_wrapper.sh new_daemon $SENTINEL/new_daemon.py\n"
                        "nohup bash $MESH/daemon_wrapper.sh ui_server", 1)
    out3, applied3, _ = patch_text(grown)
    checks.append(("a daemon added to go.sh later is covered on the next run (block regenerated)",
                   "zo_reap_unsupervised new_daemon $SENTINEL/new_daemon.py" in out3
                   and out3.count(BEGIN) == 1 and out3.count(HELPER_SEEN) == 1))
    for label, broken in (("no ERR-trap anchor", src.replace("trap '_go_err_trap' ERR", "trap x ERR")),
                          ("no purge line", src.replace("pkill -f 'daemon_wrapper.sh'", "true"))):
        o, a, pr = patch_text(broken)
        checks.append(("drift (%s) -> NOTHING changed, problem named" % label,
                       o == broken and not a and bool(pr)))
    bash = shutil.which("bash")
    if bash:
        p = subprocess.run([bash, "-n"], input=out, capture_output=True, text=True)
        checks.append(("patched go.sh parses (bash -n)", p.returncode == 0))
    # the real CLI path: write, backup, then a no-op re-run
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "go.sh"
        f.write_text(src, encoding="utf-8")
        rc1 = main(["--file", str(f)])
        after = f.read_text(encoding="utf-8")
        rc2 = main(["--file", str(f)])
        checks.append(("CLI: applies in place, writes .supbak, re-run leaves the file alone",
                       rc1 == 0 and rc2 == 0 and after == out and f.read_text(encoding="utf-8") == after
                       and (Path(td) / "go.sh.supbak").read_text(encoding="utf-8") == src))
        f.write_text(src.replace("trap '_go_err_trap' ERR", "trap x ERR"), encoding="utf-8")
        checks.append(("CLI: anchor drift -> rc 2, file untouched", main(["--file", str(f)]) == 2
                       and "zo_reap" not in f.read_text(encoding="utf-8")))
    _bash_live_test(checks)
    for name, ok in checks:
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    npass = sum(ok for _, ok in checks)
    print("supervise_go_sh self-test: %d/%d" % (npass, len(checks)))
    return 0 if npass == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    path = Path(a.file)
    if not path.exists():
        print("ERROR: %s not found" % path, file=sys.stderr)
        return 2
    src = path.read_text(encoding="utf-8")
    out, applied, problems = patch_text(src)
    for p in problems:
        print("  PROBLEM: %s" % p)
    if problems:
        return 2
    if out == src:
        print("Nothing to apply (already supervised-aware, roster unchanged). No change.")
        return 0
    if a.dry_run:
        print("[dry-run] would apply: %s" % applied)
        print("[dry-run] roster: %s" % ", ".join(n for n, _ in declared(src)))
        return 0
    bash = shutil.which("bash")
    if bash and subprocess.run([bash, "-n"], input=out, capture_output=True, text=True).returncode != 0:
        print("REFUSED: the patched go.sh does not parse (bash -n) -- NOTHING written", file=sys.stderr)
        return 2
    backup = path.with_suffix(path.suffix + ".supbak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print("Applied %s to %s (backup: %s)" % (applied, path, backup))
    print("Next `zm go` reaps unsupervised daemons right after the wrapper purge and "
          "relaunches them under daemon_wrapper.sh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
