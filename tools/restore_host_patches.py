#!/usr/bin/env python3
"""
restore_host_patches.py -- re-apply every go.sh host patch, idempotently, with no
human step. (RCA 2026-10 follow-up #2.)

WHY
  `REFRESH_MODE=reset` reverts the live /home/workspace/zo_mesh/go.sh to the
  zo_mesh repo's copy, silently dropping every host patch. On 2026-10-01 that is
  how the bootstrap-timeout patch was missing when `zm go` wedged for ~2.4h on an
  un-timed full_schema_bootstrap.py. #5937 fixed the patcher; nothing re-ran it.
  This runner is what re-runs it: ZoChainTick calls it every 4h through zo_call
  (zo-fleet-tools host_patches step), and the refresh / post_reboot path can call
  it right after a reset. On a healthy tree it is a byte-for-byte no-op.

WHAT IT RUNS (pure functions of the sibling patchers, in order)
  1. harden_go_sh.harden_text   -- every curl bounded, 3b readiness gate,
                                   git clone + EVERY full_schema_bootstrap timed
  2. supervise_go_sh.patch_text -- reap running-but-unsupervised daemons (#1)

SAFETY (docs/INCIDENT_2026-05-09.md constraints 2-4)
  * Backup before rewrite: <go.sh>.bak.<utc-iso> (last 10 kept).
  * The result must pass `bash -n` or NOTHING is written.
  * ATOMIC replace (tmp + os.replace, mode kept): a go.sh that is running right
    now keeps reading its old inode, so patching mid-boot cannot corrupt it.
    (An in-place truncate+write under a running bash script can.)
  * Size sanity: the patchers only ADD; a result smaller than the input is refused.

INVARIANTS (the handoff's acceptance, checked after every run, and by --check)
  bootstrap-timed  no full_schema_bootstrap.py call without `timeout 120`
  curl-bounded     no bare `curl -s`
  readiness-gate   section 3b present
  clone-timed      no un-timed publisher `git clone`
  reap-unsupervised  helper + generated reap block present

EXIT  0 every invariant holds (patched now, or already)
      1 (--check only) an invariant is missing -- a reset reverted it
      2 cannot restore: go.sh missing, an anchor drifted so a patch cannot
        apply, or the result failed bash -n (named; nothing unsafe written)

  python3 tools/restore_host_patches.py            # restore (what the tick runs)
  python3 tools/restore_host_patches.py --check    # read-only: rc 1 if reverted
  python3 tools/restore_host_patches.py --self-test
The last line is always `RESTORE rc=N changed=yes|no applied=[..] missing=[..]`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harden_go_sh  # noqa: E402
import supervise_go_sh  # noqa: E402

DEFAULT = "/home/workspace/zo_mesh/go.sh"
KEEP_BACKUPS = 10

INVARIANTS = [
    ("bootstrap-timed", lambda t: not re.search(
        r"(?<!timeout 120 )python3 \$SENTINEL/full_schema_bootstrap\.py", t)),
    ("curl-bounded", lambda t: not re.search(r"curl -s\b", t)),
    ("readiness-gate", lambda t: harden_go_sh.SEEN_GATE in t),
    ("clone-timed", lambda t: not re.search(
        r"(?<!timeout 60 )git clone https://github\.com/rob531/zo-sentinel\b(?!-)", t)),
    ("reap-unsupervised", lambda t: supervise_go_sh.HELPER_SEEN in t and supervise_go_sh.BEGIN in t),
]


def missing(text: str) -> list:
    return [name for name, ok in INVARIANTS if not ok(text)]


def restore_text(src: str):
    """-> (out, applied, notes). Pure: chain every patcher."""
    out, applied, notes = src, [], []
    out, a, _skipped = harden_go_sh.harden_text(out)
    applied += a
    out2, a, problems = supervise_go_sh.patch_text(out)
    if problems:
        notes += ["supervise_go_sh: %s" % p for p in problems]
    else:
        out = out2
        applied += a
    return out, applied, notes


def _bash_ok(text: str) -> bool:
    bash = shutil.which("bash")
    if not bash:
        return True   # cannot check here; the patchers' own self-tests parse their output
    return subprocess.run([bash, "-n"], input=text, capture_output=True, text=True).returncode == 0


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".%s.restore." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: Path, text: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    b = path.with_name("%s.bak.%s" % (path.name, stamp))
    b.write_text(text, encoding="utf-8")
    olds = sorted(path.parent.glob("%s.bak.2*" % path.name))
    for old in olds[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass
    return b


def run(path: Path, check_only: bool = False, log=print) -> int:
    def done(rc, changed, applied, miss, extra=""):
        log("RESTORE rc=%d changed=%s applied=%s missing=%s%s"
            % (rc, "yes" if changed else "no", applied, miss, extra))
        return rc
    if not path.exists():
        return done(2, False, [], ["go.sh"], " -- %s not found" % path)
    src = path.read_text(encoding="utf-8")
    if check_only:
        miss = missing(src)
        return done(1 if miss else 0, False, [], miss)
    out, applied, notes = restore_text(src)
    for n in notes:
        log("  note: %s" % n)
    if out != src:
        if len(out) < len(src):
            return done(2, False, applied, missing(src), " -- REFUSED: result is smaller than go.sh")
        if not _bash_ok(out):
            return done(2, False, applied, missing(src), " -- REFUSED: result fails bash -n")
        b = _backup(path, src)
        _atomic_write(path, out)
        log("  backup: %s" % b)
    miss = missing(out)
    return done(2 if miss else 0, out != src, applied, miss,
                " -- anchor drift: cannot restore %s" % miss if miss else "")


# ------------------------------------------------------------------ self-test
def self_test() -> int:
    checks = []
    snap = HERE.parent / "ops" / "host" / "go.sh"
    reverted = snap.read_text(encoding="utf-8")   # the shape a reset leaves behind
    quiet = lambda *a: None  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "go.sh"
        f.write_text(reverted, encoding="utf-8")
        os.chmod(f, 0o700)   # differs from mkstemp's 0o600, so preservation is observable
        rc_red = run(f, check_only=True, log=quiet)
        miss_red = missing(reverted)
        checks.append(("RED: a reset-reverted go.sh fails --check (rc 1), naming what is gone",
                       rc_red == 1 and "bootstrap-timed" in miss_red and "reap-unsupervised" in miss_red))
        held = open(f, encoding="utf-8")          # a go.sh that is RUNNING during the restore
        ino = f.stat().st_ino
        rc = run(f, log=quiet)
        patched = f.read_text(encoding="utf-8")
        checks.append(("GREEN: restore -> rc 0 and every invariant holds", rc == 0 and not missing(patched)))
        checks.append(("both bootstrap calls timed, curls bounded, gate + reap block present",
                       patched.count("timeout 120 python3 $SENTINEL/full_schema_bootstrap.py") >= 2
                       and not re.search(r"curl -s\b", patched)
                       and harden_go_sh.SEEN_GATE in patched and supervise_go_sh.BEGIN in patched))
        checks.append(("atomic: new inode, mode kept, a running reader still sees the OLD script",
                       f.stat().st_ino != ino and (f.stat().st_mode & 0o777) == 0o700
                       and held.read() == reverted))
        held.close()
        baks = sorted(Path(d).glob("go.sh.bak.2*"))
        checks.append(("backup before rewrite: go.sh.bak.<utc> holds the pre-restore bytes",
                       len(baks) == 1 and baks[0].read_text(encoding="utf-8") == reverted))
        rc2 = run(f, log=quiet)
        checks.append(("GREEN on a healthy tree: no-op (no change, no new backup), --check rc 0",
                       rc2 == 0 and f.read_text(encoding="utf-8") == patched
                       and len(list(Path(d).glob("go.sh.bak.2*"))) == 1
                       and run(f, check_only=True, log=quiet) == 0))
        f.write_text(reverted.replace("trap '_go_err_trap' ERR", "trap x ERR"), encoding="utf-8")
        lines = []
        rc3 = run(f, log=lines.append)
        checks.append(("drift: an anchor gone -> rc 2, named; the safe patches still land",
                       rc3 == 2 and "reap-unsupervised" in lines[-1]
                       and "timeout 120 python3 $SENTINEL/full_schema_bootstrap.py" in f.read_text(encoding="utf-8")))
        if shutil.which("bash"):
            broken = reverted + "\nif then fi\n"
            f.write_text(broken, encoding="utf-8")
            lines = []
            rc4 = run(f, log=lines.append)
            checks.append(("a result that fails bash -n is NEVER written (rc 2, file untouched)",
                           rc4 == 2 and "bash -n" in lines[-1] and f.read_text(encoding="utf-8") == broken))
        rc5 = run(Path(d) / "nope.sh", log=quiet)
        checks.append(("go.sh missing -> rc 2, never 0", rc5 == 2))
    for name, ok in checks:
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    n = sum(ok for _, ok in checks)
    print("restore_host_patches self-test: %d/%d" % (n, len(checks)))
    return 0 if n == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--check", action="store_true", help="read-only: rc 1 if a patch is missing")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    return run(Path(a.file), check_only=a.check)


if __name__ == "__main__":
    sys.exit(main())
