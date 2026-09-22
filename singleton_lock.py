"""Identity-verified single-instance lock for ZOMesh daemons.

WHY THIS EXISTS
---------------
The copy-pasted ``check_single_instance()`` shape in this repo asks::

    os.kill(old_pid, 0)          # "does SOME process own this number?"
    -> "Already running"; sys.exit(1)

That is not the question. PIDs are recycled. On 2026-09-21
``/var/run/zo/attestation_engine.pid`` held ``16646``, which the kernel had
long since reassigned to ``signal_bridge.py`` -- a different, long-lived
daemon. ``os.kill(16646, 0)`` therefore succeeded forever, attestation_engine
exited rc=1 on *every* start for 4h+, and seven independent wrapper spawners
stacked 19 respawn loops against a lock that could never clear on its own.
The daemon was declared, supervised, and structurally incapable of starting.

The question that matters is: **does the process holding that PID run THIS
script?** ``/proc/<pid>/cmdline`` answers it. This module asks that instead.

CENSUS (2026-09-21, /home/workspace/zo_sentinel on the live box): 737 files
carry the identity-free ``os.kill(pid, 0)`` staleness shape. 9 of them are
daemons on the live roster -- attestation_engine, signal_bridge, mcp_scanner,
registry_promoter_daemon, fingerprint_runner_daemon_v3, threat_intel_ingestor,
discovery_npm_paginator, ecosystems_metadata_fetcher, goose_runner. Every one
of those nine was one PID collision away from the same permanent wedge.

DESIGN NOTES
------------
* Unknown is not zero (HARNESS_DOCTRINE R6). If ``/proc/<pid>`` exists but its
  cmdline is unreadable (EPERM), we cannot *prove* the holder is not ours, so
  we treat the lock as held. That is the conservative direction: it preserves
  the old behaviour in the one case we genuinely cannot resolve, and it never
  invents a "stale" verdict it has not earned.
* No ``/proc`` (non-Linux) degrades to the legacy liveness-only answer, and
  says so via :func:`resolution_basis` so a caller can publish the basis with
  the verdict (R5).
* Takeover is logged to stderr, never silent. A lock that silently changes
  hands is how a double-run stays invisible.
"""

from __future__ import annotations

import atexit
import errno
import os
import signal
import sys
from typing import List, Optional

DEFAULT_PID_DIR = "/var/run/zo"

__all__ = [
    "acquire",
    "check_single_instance",
    "owner_matches",
    "process_cmdline",
    "read_pidfile",
    "release",
    "resolution_basis",
]


def resolution_basis() -> str:
    """How this host resolves lock ownership. Publish it with the verdict."""
    return "proc-cmdline" if os.path.isdir("/proc/self") else "kill-0-only"


def process_cmdline(pid: int) -> Optional[List[str]]:
    """Argv of ``pid``.

    Returns ``None`` when the process does not exist, and ``[]`` when it
    exists but its argv cannot be read (kernel thread, or EPERM). ``[]`` and
    ``None`` are deliberately different values -- see R6.
    """
    if not os.path.isdir("/proc/self"):
        return None
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except (PermissionError, OSError):
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return True  # exists, owned by someone else
        return False
    return True


def owner_matches(pid: int, script: str) -> bool:
    """True if ``pid`` is a live process that is running ``script``.

    False means the pidfile is STALE -- either nothing holds that PID, or the
    holder is some other program that inherited the number.
    """
    if pid <= 0:
        return False

    cmdline = process_cmdline(pid)

    if cmdline is None:
        # No /proc at all -> we only know liveness. No /proc entry -> dead.
        if resolution_basis() == "kill-0-only":
            return _pid_is_alive(pid)
        return False

    if not cmdline:
        # Exists but unreadable. Cannot disprove ownership -> assume held.
        return True

    wanted = os.path.basename(script)
    return any(os.path.basename(arg) == wanted for arg in cmdline)


def read_pidfile(path: str) -> Optional[int]:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _pid_path(service_name: str, pid_dir: str) -> str:
    return os.path.join(pid_dir, "%s.pid" % service_name)


def acquire(
    service_name: str,
    script: Optional[str] = None,
    pid_dir: str = DEFAULT_PID_DIR,
) -> bool:
    """Take the single-instance lock for ``service_name``.

    Returns True when the lock is ours (including when a stale pidfile was
    taken over), False when a genuine live instance of ``script`` holds it.
    Never raises on a missing or corrupt pidfile -- an unreadable lock is a
    stale lock, not a reason to refuse to start.
    """
    script = script or os.path.abspath(sys.argv[0])
    try:
        os.makedirs(pid_dir, exist_ok=True)
    except OSError:
        pid_dir = "/tmp"
        os.makedirs(pid_dir, exist_ok=True)

    pid_file = _pid_path(service_name, pid_dir)
    old_pid = read_pidfile(pid_file)

    if old_pid is not None and old_pid != os.getpid():
        if owner_matches(old_pid, script):
            return False
        holder = process_cmdline(old_pid)
        if holder:
            sys.stderr.write(
                "[%s] stale lock taken over: pid %d is %r, not %s (basis=%s)\n"
                % (
                    service_name,
                    old_pid,
                    " ".join(holder),
                    os.path.basename(script),
                    resolution_basis(),
                )
            )
        else:
            sys.stderr.write(
                "[%s] stale lock taken over: pid %d is not running (basis=%s)\n"
                % (service_name, old_pid, resolution_basis())
            )

    tmp = "%s.%d.tmp" % (pid_file, os.getpid())
    with open(tmp, "w") as fh:
        fh.write(str(os.getpid()))
    os.replace(tmp, pid_file)

    _install_cleanup(pid_file)
    return True


def release(pid_file: str) -> None:
    """Remove ``pid_file`` only if we still own it."""
    if read_pidfile(pid_file) == os.getpid():
        try:
            os.remove(pid_file)
        except OSError:
            pass


def _install_cleanup(pid_file: str) -> None:
    atexit.register(release, pid_file)

    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)

        def handler(signum, frame, _prev=previous, _f=pid_file):
            release(_f)
            if callable(_prev) and _prev not in (signal.SIG_IGN, signal.SIG_DFL):
                _prev(signum, frame)
            else:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # not the main thread; atexit still covers us


def check_single_instance(
    service_name: str,
    script: Optional[str] = None,
    pid_dir: str = DEFAULT_PID_DIR,
) -> bool:
    """Drop-in for the legacy shape: exit(1) if a real instance is running.

    The legacy version exited on *any* live PID. This one exits only when the
    holder is actually running the same script.
    """
    if acquire(service_name, script=script, pid_dir=pid_dir):
        return True
    old_pid = read_pidfile(_pid_path(service_name, pid_dir))
    print("Already running with PID %s" % old_pid)
    sys.exit(1)
