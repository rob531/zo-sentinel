"""Hermetic tests for tools/install_catalog_supervisord.sh.

WHY THIS FILE EXISTS
    FU-389: the host has NO cron daemon. All three crontab triggers for
    bus_catalog_guard.sh are inert. The fix is a supervisord [program:bus-catalog-guard]
    block registered via install_catalog_supervisord.sh.

    These tests verify the script's contract without touching /etc/zo or a live
    supervisord. Every test runs purely in a tempdir, using the SUPERVISORD_USER_CONF
    and SKIP_SUPERVISORCTL env vars the script exposes for exactly this purpose.

THREE TESTS
    1. shell-syntax   -- bash -n must pass (catches "I edited it and broke it")
    2. --dry-run      -- exits 0 and does NOT modify the conf file
    3. idempotency    -- two consecutive runs produce exactly one [program:bus-catalog-guard]
                         block (not duplicated)
"""
import os
import re
import subprocess
import sys
import tempfile
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "install_catalog_supervisord.sh"

# These tests invoke a POSIX shell script.  On Windows the PATH puts WSL's
# bash.exe first, which requires an installed WSL distro (unavailable in CI or
# on a bare tower).  The script itself targets a Linux host; the tests run in
# CI on ubuntu-latest where /usr/bin/bash is the right POSIX shell.
_POSIX_BASH = pytest.mark.skipif(
    sys.platform == "win32",
    reason="shell script tests require a POSIX bash; skipped on Windows",
)

FAKE_CONF_CONTENT = """\
[inet_http_server]
port=127.0.0.1:29011

[supervisord]
logfile=/dev/shm/supervisord_user.log
"""


def _run(extra_env, args=(), *, check=False):
    env = {**os.environ, **extra_env}
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, SCRIPT,
            output=result.stdout, stderr=result.stderr,
        )
    return result


@_POSIX_BASH
def test_script_has_valid_shell_syntax():
    """bash -n must report no syntax errors."""
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (
        f"shell syntax check failed:\n{r.stderr}"
    )


@_POSIX_BASH
def test_dry_run_does_not_modify_conf():
    """--dry-run exits 0 and leaves the conf file untouched."""
    with tempfile.TemporaryDirectory() as td:
        conf = pathlib.Path(td) / "supervisord-user.conf"
        conf.write_text(FAKE_CONF_CONTENT)
        mtime_before = conf.stat().st_mtime

        env = {
            "SUPERVISORD_USER_CONF": str(conf),
            "ZO_REPO": td,
            "ZO_CATALOG_LOG": str(pathlib.Path(td) / "refresh.log"),
            "SKIP_SUPERVISORCTL": "1",
        }
        r = _run(env, args=["--dry-run"])

        assert r.returncode == 0, f"--dry-run failed:\nstdout={r.stdout}\nstderr={r.stderr}"
        assert conf.stat().st_mtime == mtime_before, (
            "--dry-run must not modify the conf file"
        )


@_POSIX_BASH
def test_idempotent_write_no_duplicate_block():
    """Running install twice produces exactly one [program:bus-catalog-guard] block."""
    with tempfile.TemporaryDirectory() as td:
        conf = pathlib.Path(td) / "supervisord-user.conf"
        conf.write_text(FAKE_CONF_CONTENT)

        env = {
            "SUPERVISORD_USER_CONF": str(conf),
            "ZO_REPO": td,
            "ZO_CATALOG_LOG": str(pathlib.Path(td) / "refresh.log"),
            "SKIP_SUPERVISORCTL": "1",
        }

        # First run -- must write the block.
        r1 = _run(env)
        assert r1.returncode == 0, (
            f"first run failed:\nstdout={r1.stdout}\nstderr={r1.stderr}"
        )
        content_after_first = conf.read_text()
        assert "[program:bus-catalog-guard]" in content_after_first, (
            "first run must write [program:bus-catalog-guard]"
        )

        # Second run -- must NOT duplicate the block.
        r2 = _run(env)
        assert r2.returncode == 0, (
            f"second run failed:\nstdout={r2.stdout}\nstderr={r2.stderr}"
        )
        content_after_second = conf.read_text()
        count = len(re.findall(r"\[program:bus-catalog-guard\]", content_after_second))
        assert count == 1, (
            f"expected 1 [program:bus-catalog-guard] block after two runs, got {count}:\n"
            f"{content_after_second}"
        )
