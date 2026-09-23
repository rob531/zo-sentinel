"""Negative controls for the identity-verified single-instance lock.

HARNESS_DOCTRINE R4: an assertion never seen RED is not evidence. Each test
below names, in its docstring, the condition under which it fails, and
``test_legacy_shape_is_the_defect`` exercises the OLD logic on purpose so the
defect is observed going red inside the suite rather than only in production.

Reproduces the 2026-09-21 wedge: /var/run/zo/attestation_engine.pid held a PID
the kernel had recycled to signal_bridge.py.
"""

import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import singleton_lock  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.isdir("/proc/self"),
    reason="identity resolution needs /proc",
)


@pytest.fixture
def impostor():
    """A live process that is NOT our daemon -- the recycled-PID stand-in."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        if os.path.isdir("/proc/%d" % proc.pid):
            break
        time.sleep(0.05)
    yield proc.pid
    proc.kill()
    proc.wait()


def _legacy_would_refuse(pid):
    """The exact shape this change replaces, kept so it can be seen failing."""
    try:
        os.kill(pid, 0)
        return True  # "Already running with PID ..." -> sys.exit(1)
    except OSError:
        return False


def test_legacy_shape_is_the_defect(impostor):
    """NEGATIVE CONTROL. Red means the bug is real.

    The legacy check refuses to start because *some* process owns the number,
    even though that process is demonstrably a different program. This test
    asserts the defect exists; if it ever fails, the legacy shape is gone and
    this file should be deleted along with it.
    """
    assert _legacy_would_refuse(impostor) is True
    holder = singleton_lock.process_cmdline(impostor)
    assert holder, "impostor must be readable for this control to mean anything"
    assert not any(
        os.path.basename(arg) == "attestation_engine.py" for arg in holder
    ), "impostor must not be the daemon, or the control proves nothing"


def test_stale_lock_held_by_another_program_is_taken_over(impostor, tmp_path):
    """Fails if a recycled PID can still wedge a daemon shut."""
    pid_dir = str(tmp_path)
    with open(os.path.join(pid_dir, "attestation_engine.pid"), "w") as fh:
        fh.write(str(impostor))

    assert singleton_lock.owner_matches(impostor, "attestation_engine.py") is False
    assert (
        singleton_lock.acquire(
            "attestation_engine", script="attestation_engine.py", pid_dir=pid_dir
        )
        is True
    )
    assert singleton_lock.read_pidfile(
        os.path.join(pid_dir, "attestation_engine.pid")
    ) == os.getpid()


def test_a_real_duplicate_is_still_refused(tmp_path):
    """NEGATIVE CONTROL in the other direction.

    A guard that always says yes is not a guard. Red means the new lock has
    stopped excluding genuine second instances.
    """
    pid_dir = str(tmp_path)
    script = os.path.abspath(sys.argv[0]) or sys.executable
    holder = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if os.path.isdir("/proc/%d" % holder.pid):
                break
            time.sleep(0.05)
        # The holder IS running python3 -- lock against that identity.
        with open(os.path.join(pid_dir, "twin.pid"), "w") as fh:
            fh.write(str(holder.pid))
        assert (
            singleton_lock.acquire("twin", script=sys.executable, pid_dir=pid_dir)
            is False
        )
    finally:
        holder.kill()
        holder.wait()


def test_dead_pid_is_stale(tmp_path):
    """Fails if a lock left by a crashed daemon survives its owner."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead = proc.pid
    pid_dir = str(tmp_path)
    with open(os.path.join(pid_dir, "gone.pid"), "w") as fh:
        fh.write(str(dead))
    assert singleton_lock.owner_matches(dead, "gone.py") is False
    assert singleton_lock.acquire("gone", script="gone.py", pid_dir=pid_dir) is True


def test_corrupt_pidfile_does_not_block_startup(tmp_path):
    """Fails if a truncated pidfile can keep a daemon down."""
    pid_dir = str(tmp_path)
    with open(os.path.join(pid_dir, "junk.pid"), "w") as fh:
        fh.write("not-a-pid")
    assert singleton_lock.read_pidfile(os.path.join(pid_dir, "junk.pid")) is None
    assert singleton_lock.acquire("junk", script="junk.py", pid_dir=pid_dir) is True


def test_release_only_removes_our_own_lock(tmp_path):
    """Fails if a losing starter can delete the winner's lock."""
    pid_file = os.path.join(str(tmp_path), "other.pid")
    with open(pid_file, "w") as fh:
        fh.write("999999")
    singleton_lock.release(pid_file)
    assert os.path.exists(pid_file), "must not delete a lock we do not own"
