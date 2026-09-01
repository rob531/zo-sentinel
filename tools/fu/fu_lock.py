#!/usr/bin/env python3
"""Safe concurrent edits to FOLLOWUPS.md. No database required.

Why this exists
---------------
The ledger is edited by several scheduled tasks, each doing
read-whole-file -> mutate -> write-whole-file. That is a lost-update race:
task A reads at T0, B reads at T1, A writes at T2, B writes at T3, and A's
change is gone with no error anywhere. It is not hypothetical --
`ops_audit_state.clobbered-2026-07-28.bak.json` is this morning, on the same
pattern, on a different file. On 2026-07-28 the FU tooling
(ledger_lint / fu_seed_predicates / fu_verify) added three MORE full-file
writers, making it worse.

There is no serialisation primitive available on the tower: no PostgreSQL is
installed (no service, no process, no psql), the write-service bus on 8772
lives on the ZoComputer runtime, and the only DB reachable from here is PROD
via a flyctl proxy on 15432 -- which must never be load-bearing for agent
coordination.

None of that is needed. Mutual exclusion between processes on ONE host is a
local problem with a local answer:

  1. an O_EXCL lock file (atomic create is the compare-and-swap), with a PID
     and a TTL so a crashed holder cannot wedge the ledger forever, and
  2. optimistic concurrency -- hash on read, re-hash before write, refuse to
     write if the file changed underneath, and
  3. atomic replace via os.replace, so a crash mid-write cannot truncate a
     740KB ledger.

There is a second, subtler writer: **Syncthing** (the tower<->ZoComputer
bridge) also writes this file. A lock cannot stop it, because it is a
different host. What the hash check DOES stop is this process silently
overwriting a version that arrived from the other side between our read and
our write. Cross-host edits still need the per-writer-event design; this
module makes the single-host case correct and the cross-host case loud.

Usage
-----
    with ledger_txn(path) as txn:
        txn.lines = mutate(txn.lines)
    # commits on clean exit; raises LedgerChanged if the file moved underneath
"""
from __future__ import annotations

import errno
import hashlib
import os
import time
from contextlib import contextmanager

LOCK_TTL_S = 300          # a holder older than this is presumed dead
ACQUIRE_TIMEOUT_S = 60
POLL_S = 0.25


class LedgerBusy(RuntimeError):
    """Another process holds the lock and did not release it in time."""


class LedgerChanged(RuntimeError):
    """The file changed between our read and our write. Nothing was written."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lock_path(path: str) -> str:
    return path + ".lock"


def _read_holder(lp: str):
    try:
        with open(lp, encoding="utf-8") as fh:
            pid, ts = fh.read().split(",", 1)
        return int(pid), float(ts)
    except (OSError, ValueError):
        return None, None


def acquire(path: str, timeout: int = ACQUIRE_TIMEOUT_S) -> str:
    lp = _lock_path(path)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as fh:
                fh.write("%d,%f" % (os.getpid(), time.time()))
            return lp
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            pid, ts = _read_holder(lp)
            if ts is not None and (time.time() - ts) > LOCK_TTL_S:
                # Stale holder. Reclaim, but say so -- a lock that silently
                # steals itself is how a "fixed" race comes back.
                try:
                    os.unlink(lp)
                    continue
                except OSError:
                    pass
            if time.time() > deadline:
                raise LedgerBusy(
                    "%s held by pid %s since %s; gave up after %ss"
                    % (lp, pid, ts, timeout))
            time.sleep(POLL_S)


def release(lp: str) -> None:
    try:
        os.unlink(lp)
    except OSError:
        pass


class _Txn:
    def __init__(self, path, lines, dig):
        self.path, self.lines, self._dig = path, lines, dig
        self.committed = False

    @property
    def original_digest(self) -> str:
        return self._dig


# ---------------------------------------------------------------------------
# PORTED FROM THE TOWER-LOCAL COPY 2026-09-01 (prod-drift-sentinel).
# FU-212 measured and cured this in July, but the cure only ever existed in
# _tools\fu_lock.py, which is not in any git repo. The repo copy -- this file,
# the one a fresh clone and CI actually run -- never had it. Two writers were
# wired to "the existing cure" today and failed with NameError, which is how
# the divergence surfaced. Algorithm unchanged; only its address is new.
# ---------------------------------------------------------------------------
#: strategy used by the most recent successful commit -- "replace" (the plain
#: atomic rename) or "swap" (the mapped-destination fallback below). Read by
#: tests and by triage, so the fallback firing is OBSERVABLE and not silent.
last_commit_strategy = None


def _replace_with_fallback(tmp: str, path: str, attempts: int = 4) -> str:
    """Move `tmp` onto `path`, surviving a destination that cannot be replaced.

    WHY THIS EXISTS (FU-212, measured 2026-07-31, not assumed). On Windows
    `os.replace` is MoveFileEx(REPLACE_EXISTING), and that call is refused with
    ERROR_ACCESS_DENIED (WinError 5) when the DESTINATION carries a mapped
    section -- even though the same file can be opened r+b AND plainly renamed.
    The Claude desktop app (RestartManager named pid 19004) holds exactly such a
    map on FOLLOWUPS.md, so every ledger writer -- ledger_lint, fu_verify,
    fu_ledger.append_log -- failed 100% of the time inside a session while
    passing every test outside one.

    The three facts the fallback rests on were each observed directly:
        replace(fresh -> fresh dest)      OK
        replace(fresh -> NON-existent)    OK
        rename(FOLLOWUPS.md -> other)     OK
        replace(fresh -> FOLLOWUPS.md)    DENIED
    So: free the destination NAME by renaming it aside, replace into the now
    free name, and only then retire the parked copy. Nothing is deleted while
    anything can still fail -- on any error the parked original is put back, so
    the worst case is the pre-existing "nothing was written", never a loss.

    Returns the strategy that carried the write: "replace" or "swap".
    """
    global last_commit_strategy
    last = None
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            last_commit_strategy = "replace"
            return "replace"
        except PermissionError as exc:      # WinError 5 (denied) / 32 (sharing)
            last = exc
            time.sleep(0.2 * (2 ** i))      # a genuinely transient scanner hold

    # Persistent. Fall back to the rename-swap.
    parked = path + ".prev"
    try:
        os.replace(path, parked)            # dest name is now free
    except OSError:
        raise last                          # cannot even move it: report the original
    try:
        os.replace(tmp, path)
    except OSError:
        os.replace(parked, path)            # put the original back, byte for byte
        raise last
    last_commit_strategy = "swap"
    return "swap"


#: Public name. FU-212's cure sat behind a private one and was wired into a
#: single call site; a private name is what tells the next writer not to ask
#: (FU-343 -- a cure wired into one door of many reads as a cure).
replace_with_fallback = _replace_with_fallback


@contextmanager
def ledger_txn(path: str, timeout: int = ACQUIRE_TIMEOUT_S, write: bool = True):
    """Lock, read, yield a txn, then commit atomically if unchanged."""
    lp = acquire(path, timeout) if write else None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        txn = _Txn(path, text.split("\n"), digest(text))
        yield txn
        if not write:
            return
        new_text = "\n".join(txn.lines)
        if digest(new_text) == txn.original_digest:
            txn.committed = True          # nothing to do; still a success
            return
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        if digest(current) != txn.original_digest:
            raise LedgerChanged(
                "%s changed under us (likely the Syncthing bridge or another "
                "task). Nothing written -- re-run to pick up their edit." % path)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        _replace_with_fallback(tmp, path)
        txn.committed = True
    finally:
        if lp:
            release(lp)
