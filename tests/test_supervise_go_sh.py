"""RCA 2026-10 follow-up #1: go.sh relaunches a running-but-UNSUPERVISED daemon.

Two-pole (tools/supervise_go_sh.py --self-test): RED -- go.sh's own "already
running" check reads an orphan as healthy and skips it; GREEN -- the inserted
helper, run for real under go.sh's `set -eE` + ERR trap, kills only the orphan
(never a supervised daemon, never a bystander) and the check falls through to
LAUNCH. Text half: the 5 orphans of 2026-10-01 are covered, idempotent, a
drifted go.sh is left untouched.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_supervise_go_sh_two_pole_self_test():
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "supervise_go_sh.py"), "--self-test"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "FAIL" not in p.stdout
