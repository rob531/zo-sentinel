"""RCA 2026-10 follow-up #2: go.sh host patches re-apply themselves after a reset.

Two-pole (tools/restore_host_patches.py --self-test): RED -- the reset-reverted
go.sh fails --check, naming the lost patches; GREEN -- one run restores every
invariant (both bootstrap calls timed, curls bounded, readiness gate, reap
block) atomically with a backup, and a second run on the healthy tree is a
byte-for-byte no-op. A result that fails bash -n is never written.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_restore_host_patches_two_pole_self_test():
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "restore_host_patches.py"), "--self-test"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "FAIL" not in p.stdout
