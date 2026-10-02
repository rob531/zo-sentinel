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
  NOT covered: the FEATURE-wiring patchers (patch_go_sh.py -- trio launch, keyed
  ladder_shim, 12.5c promoter; patch_go_sh_tlog.py). They are operator-run
  deploy steps (tomorrow_deploy.sh), not the hardening a reset must not lose,
  and --check does not claim them.

SAFETY (docs/INCIDENT_2026-05-09.md)
  * Constraint 1 (no unreviewed write to a deployment path): this is a
    host-side PATCHER of the harden_go_sh class -- every byte it writes is code
    reviewed in this repo. Unattended invocation (ZoChainTick) is OPT-IN.
  * Constraint 2, backup before rewrite: an exact byte copy in a directory
    OUTSIDE the zo_mesh git tree (a `git clean` there must not eat it),
    go.sh.restore-bak.<utc>. Only files matching that exact pattern are ever
    pruned (last 10) -- an operator's own go.sh.bak.* is never touched.
  * The result must pass `bash -n` or NOTHING is written.
  * ATOMIC replace (tmp + fsync + os.replace + dir fsync; mode, owner, group
    kept; a symlinked go.sh is patched at its TARGET): a go.sh that is running
    keeps its old inode, so patching mid-boot cannot corrupt it.
  * Bytes are kept exactly (no newline translation: CR/CRLF survive).
  * Size sanity: outside the regenerated reap block the patchers only ADD; a
    result that shrinks there is refused. (The block itself legitimately
    shrinks when a daemon leaves go.sh.)
  * Any exception still ends in a RESTORE rc=2 line, never a bare traceback.

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
import errno
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
BACKUP_DIR = "/home/workspace/.go_sh_backups"   # outside the zo_mesh git tree
KEEP_BACKUPS = 10
BACKUP_RE = re.compile(r"^go\.sh\.restore-bak\.\d{8}T\d{12}Z$")

_BOOT = re.compile(r"(timeout \d+ )?python3\s+[\"']?\$\{?SENTINEL\}?/full_schema_bootstrap\.py")
INVARIANTS = [
    # any spelling ($SENTINEL, ${SENTINEL}, quoted) must carry some `timeout N`
    ("bootstrap-timed", lambda t: all(m.group(1) for m in _BOOT.finditer(t))),
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


def _outside_block(text: str) -> str:
    """go.sh minus the regenerated reap block (the one part allowed to shrink)."""
    b, e = supervise_go_sh.BEGIN, supervise_go_sh.END
    i = text.find(b)
    j = text.find(e, i) if i >= 0 else -1
    return text if i < 0 or j < 0 else text[:i] + text[j + len(e):]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:   # no newline translation
        return fh.read()


def _bash_ok(text: str) -> bool:
    bash = shutil.which("bash")
    if not bash:
        return True   # cannot check here; the patchers' own self-tests parse their output
    return subprocess.run([bash, "-n"], input=text, capture_output=True, text=True).returncode == 0


def _atomic_write(path: Path, text: str) -> None:
    st = path.stat()
    fd, tmp = tempfile.mkstemp(prefix=".%s.restore." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except (PermissionError, AttributeError):
            pass   # best effort: not root, or not POSIX
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:   # make the rename itself durable
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _backup(path: Path, backup_dir: Path) -> Path:
    """An EXACT byte copy (metadata too), outside the repo tree. A partial copy
    (disk full) is removed -- it must never look like a good backup."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    b = backup_dir / ("%s.restore-bak.%s" % (path.name, stamp))
    try:
        shutil.copy2(str(path), str(b))
    except BaseException:
        try:
            b.unlink()
        except OSError:
            pass
        raise
    return b


def _prune(backup_dir: Path) -> None:
    mine = sorted(p for p in backup_dir.iterdir() if BACKUP_RE.match(p.name))
    for old in mine[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def run(path: Path, check_only: bool = False, log=print, backup_dir: Path = None) -> int:
    def done(rc, changed, applied, miss, extra=""):
        log("RESTORE rc=%d changed=%s applied=%s missing=%s%s"
            % (rc, "yes" if changed else "no", applied, miss, extra))
        return rc
    try:
        return _run(Path(os.path.realpath(path)), check_only, log, done,
                    Path(backup_dir or BACKUP_DIR))
    except Exception as exc:  # noqa: BLE001 -- the last line is ALWAYS a RESTORE line
        hint = " (disk full)" if getattr(exc, "errno", None) == errno.ENOSPC else ""
        return done(2, False, [], ["?"], " -- %s: %s%s; go.sh left as it was"
                    % (exc.__class__.__name__, str(exc)[:200], hint))


def _run(path: Path, check_only, log, done, backup_dir: Path) -> int:
    if not path.exists():
        return done(2, False, [], ["go.sh"], " -- %s not found" % path)
    src = _read(path)
    if check_only:
        miss = missing(src)
        return done(1 if miss else 0, False, [], miss)
    out, applied, notes = restore_text(src)
    for n in notes:
        log("  note: %s" % n)
    if out != src:
        if len(_outside_block(out)) < len(_outside_block(src)):
            return done(2, False, applied, missing(src), " -- REFUSED: result shrinks go.sh")
        if not _bash_ok(out):
            return done(2, False, applied, missing(src), " -- REFUSED: result fails bash -n")
        b = _backup(path, backup_dir)
        _atomic_write(path, out)
        _prune(backup_dir)
        log("  backup: %s" % b)
    miss = missing(out)
    return done(2 if miss else 0, out != src, applied, miss,
                " -- anchor drift: cannot restore %s" % miss if miss else "")


# ------------------------------------------------------------------ self-test
def self_test() -> int:
    checks = []
    snap = HERE.parent / "ops" / "host" / "go.sh"
    reverted = _read(snap)                        # the shape a reset leaves behind
    quiet = lambda *a: None  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        bk = d / "backups"
        f = d / "zo_mesh" / "go.sh"
        f.parent.mkdir()
        f.write_text(reverted, encoding="utf-8")
        os.chmod(f, 0o700)   # differs from mkstemp's 0o600, so preservation is observable
        R = lambda path=f, **k: run(path, log=k.pop("log", quiet), backup_dir=bk, **k)  # noqa: E731
        rc_red = R(check_only=True)
        miss_red = missing(reverted)
        checks.append(("RED: a reset-reverted go.sh fails --check (rc 1), naming what is gone",
                       rc_red == 1 and "bootstrap-timed" in miss_red and "reap-unsupervised" in miss_red))
        held = open(f, encoding="utf-8")          # a go.sh that is RUNNING during the restore
        ino = f.stat().st_ino
        rc = R()
        patched = _read(f)
        checks.append(("GREEN: restore -> rc 0 and every invariant holds", rc == 0 and not missing(patched)))
        checks.append(("both bootstrap calls timed, curls bounded, gate + reap block present",
                       patched.count("timeout 120 python3 $SENTINEL/full_schema_bootstrap.py") >= 2
                       and not re.search(r"curl -s\b", patched)
                       and harden_go_sh.SEEN_GATE in patched and supervise_go_sh.BEGIN in patched))
        checks.append(("atomic: new inode, mode kept, a running reader still sees the OLD script",
                       f.stat().st_ino != ino and (f.stat().st_mode & 0o777) == 0o700
                       and held.read() == reverted))
        held.close()
        baks = sorted(bk.glob("go.sh.restore-bak.*"))
        checks.append(("backup before rewrite: an exact byte copy, OUTSIDE the go.sh (git) directory",
                       len(baks) == 1 and baks[0].read_bytes() == reverted.encode("utf-8")
                       and not list(f.parent.glob("*bak*"))))
        rc2 = R()
        checks.append(("GREEN on a healthy tree: no-op (no change, no new backup), --check rc 0",
                       rc2 == 0 and _read(f) == patched and len(list(bk.glob("go.sh.restore-bak.*"))) == 1
                       and R(check_only=True) == 0))

        # a daemon LEAVES go.sh (patch_go_sh.py's keyed ladder_shim): the reap block shrinks
        gone = patched.replace("nohup bash $MESH/daemon_wrapper.sh ladder_shim $SENTINEL/ladder_shim.py",
                               "nohup bash $MESH/ladder_shim_with_keys.sh")
        f.write_text(gone, encoding="utf-8")
        lines = []
        rc3 = R(log=lines.append)
        checks.append(("a daemon leaving go.sh SHRINKS the reap block -> accepted, block regenerated",
                       rc3 == 0 and "zo_reap_unsupervised ladder_shim " not in _read(f)))
        f.write_text(reverted, encoding="utf-8")
        real_rt = globals()["restore_text"]
        globals()["restore_text"] = lambda t: (t.replace('hdr "2. Ollama"', "", 1), ["bad"], [])
        lines = []
        try:
            rc_s = R(log=lines.append)
        finally:
            globals()["restore_text"] = real_rt
        checks.append(("a patcher that would SHRINK go.sh outside the block is refused (rc 2, untouched)",
                       rc_s == 2 and "shrinks" in lines[-1] and _read(f) == reverted))

        # an operator's own backups are never pruned, whatever their name
        theirs = ["go.sh.bak.2026-06-05-pre-651-firefight", "go.sh.bak.20260528_tailscale", "go.sh.bak.2.9.1",
                  "go.sh.restore-bak.handmade"]
        for n in theirs:
            (bk / n).write_text("operator", encoding="utf-8")
        for k in range(KEEP_BACKUPS + 3):
            f.write_text(reverted + "\n# run %d\n" % k, encoding="utf-8")
            R()
        mine = [p for p in bk.iterdir() if BACKUP_RE.match(p.name)]
        checks.append(("pruning keeps the last %d of ITS OWN backups and never touches any other file"
                       % KEEP_BACKUPS, len(mine) == KEEP_BACKUPS and all((bk / n).exists() for n in theirs)))

        # a SYMLINKED go.sh is patched at its target; the link stays a link
        real = d / "real_go.sh"
        real.write_text(reverted, encoding="utf-8")
        link = d / "zo_mesh" / "go_link.sh"
        link.symlink_to(real)
        R(link)
        checks.append(("a symlinked go.sh: the TARGET is patched, the link is still a link",
                       link.is_symlink() and not missing(_read(real))))

        # bytes kept exactly: a CR inside a line survives, backup is the exact original
        cr = reverted.replace("hdr \"2. Ollama\"", "hdr \"2. Ollama\"\r", 1)
        f.write_bytes(cr.encode("utf-8"))
        n_cr = cr.count("\r")
        R()
        newest = sorted(p for p in bk.iterdir() if BACKUP_RE.match(p.name))[-1]
        checks.append(("CR bytes survive the restore (no newline translation) and the backup is exact",
                       n_cr and f.read_bytes().count(b"\r") == n_cr and newest.read_bytes() == cr.encode("utf-8")))

        # owner/group kept (only observable as root)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            f.write_text(reverted, encoding="utf-8")
            os.chown(f, 65534, 65534)
            R()
            checks.append(("owner and group survive the atomic replace",
                           (f.stat().st_uid, f.stat().st_gid) == (65534, 65534)))

        # any exception still ends in a RESTORE line (here: a non-UTF-8 go.sh)
        f.write_bytes(b"#!/bin/bash\n\xff\xfe broken\n")
        lines = []
        rc6 = R(log=lines.append)
        checks.append(("an exception (non-UTF-8 go.sh) -> rc 2 with a RESTORE line, file untouched",
                       rc6 == 2 and lines[-1].startswith("RESTORE rc=2") and f.read_bytes().startswith(b"#!/bin/bash\n\xff")))

        # drift and refusal
        f.write_text(reverted.replace("trap '_go_err_trap' ERR", "trap x ERR"), encoding="utf-8")
        lines = []
        rc7 = R(log=lines.append)
        checks.append(("drift: an anchor gone -> rc 2, named; the safe patches still land",
                       rc7 == 2 and "reap-unsupervised" in lines[-1]
                       and "timeout 120 python3 $SENTINEL/full_schema_bootstrap.py" in _read(f)))
        if shutil.which("bash"):
            broken = reverted + "\nif then fi\n"
            f.write_text(broken, encoding="utf-8")
            lines = []
            rc8 = R(log=lines.append)
            checks.append(("a result that fails bash -n is NEVER written (rc 2, file untouched)",
                           rc8 == 2 and "bash -n" in lines[-1] and _read(f) == broken))
        checks.append(("go.sh missing -> rc 2, never 0", R(d / "nope.sh") == 2))
    checks.append(("bootstrap-timed sees every spelling ($SENTINEL, ${SENTINEL}, quoted) and any `timeout N`",
                   missing('python3 "${SENTINEL}/full_schema_bootstrap.py" 2>&1\\n') == ["bootstrap-timed",
                   "readiness-gate", "reap-unsupervised"]
                   and "bootstrap-timed" not in missing("timeout 180 python3 $SENTINEL/full_schema_bootstrap.py\\n")))
    for name, ok in checks:
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    n = sum(bool(ok) for _, ok in checks)
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
