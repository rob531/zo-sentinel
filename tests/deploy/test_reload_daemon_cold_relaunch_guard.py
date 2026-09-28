"""Regression test: the cold-relaunch fallback in tools/reload_daemon.sh must
never report OK for a pid it did not actually replace.

Measured live 2026-09-28 on the deploy-runtime-from-main lane: after the ff to
f5d7da19a, `reload_daemon.sh sentinel_directive_generator_goose` printed
"OK: sentinel_directive_generator_goose running (pid 4602)" and exited 0 --
but 4602 was the SAME pre-ff process (started 04:21:09), and
daemon_staleness.py still reported it STALE two minutes later. The post-verify
loop required a pid different from the one it killed; the cold-relaunch
FALLBACK loop did not, so a surviving old process was accepted as proof of a
reload. R2: a success line is not an arming.

The test extracts the live guard out of the script by anchor, so it cannot
drift away from the shipped code: if the anchors disappear, this fails loudly.
Two poles, per the two-point standard --
  SURVIVOR  : pgrep always yields the same pre-existing live pid -> MUST NOT be OK
  RELAUNCHED: pgrep yields a genuinely new pid -> MUST be OK, rc 0
"""

import os
import re
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "tools", "reload_daemon.sh")

CAPTURE = '  PRE_COLD=$(pgrep -f "$CHILD_PAT" 2>/dev/null | head -1 || true)'
LOOP_START = "  for _ in $(seq 1 20); do"
REFUSAL_END = "    exit 5\n  fi\n"


def _extract_guard():
    """Return the live capture line + the fallback verify loop and its refusal
    branch, lifted verbatim from the shipped script. The relaunch plumbing in
    between (pkill / nohup wrapper) is deliberately NOT executed."""
    src = open(SCRIPT, encoding="utf-8").read()
    assert CAPTURE in src, (
        "PRE_COLD capture missing from %s -- the cold-relaunch fallback has lost "
        "its pid guard, which is exactly the 2026-09-28 false-OK regression." % SCRIPT
    )
    tail = src[src.index(CAPTURE) + len(CAPTURE):]
    assert LOOP_START in tail, "fallback verify loop not found after the PRE_COLD capture."
    block = tail[tail.index(LOOP_START):]
    assert REFUSAL_END in block, (
        "the refusal branch (exit 5) is gone from the cold-relaunch fallback -- a "
        "surviving old process would again be reported as a successful reload."
    )
    block = block[: block.index(REFUSAL_END) + len(REFUSAL_END)]
    assert "PRE_COLD:-__none__" in block, (
        "the fallback verify loop no longer compares against PRE_COLD."
    )
    # keep the wait bounded in test: 20 one-second polls would be dead time
    block = block.replace("$(seq 1 20)", "$(seq 1 3)")
    return CAPTURE + "\n" + block


def _harness(tmp_path, guard, pgrep_body):
    shim = tmp_path / "bin"
    shim.mkdir()
    p = shim / "pgrep"
    p.write_text(pgrep_body)
    p.chmod(0o755)

    script = tmp_path / "case.sh"
    script.write_text(
        textwrap.dedent(
            """\
            set -uo pipefail
            CHILD_PAT="ZZ_RELOAD_GUARD_TESTPAT"
            LOGS=%s
            NAME=testdaemon
            NEW=""
            """
            % tmp_path
        )
        # the extracted guard is indented for its `if` block; dedent one level
        + "\n".join(l[2:] if l.startswith("  ") else l for l in guard.splitlines())
        + textwrap.dedent(
            """
            if [ -n "$NEW" ]; then echo "OK: running (pid $NEW)"; exit 0; fi
            echo "DOWN"; exit 1
            """
        )
    )
    env = dict(os.environ, PATH="%s:%s" % (shim, os.environ["PATH"]))
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=120
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell guard")
def test_survivor_pid_is_not_reported_as_a_successful_reload(tmp_path):
    """The live 2026-09-28 failure: nothing was replaced, so nothing is OK."""
    victim = subprocess.Popen(["sleep", "120"])
    try:
        r = _harness(
            tmp_path,
            _extract_guard(),
            "#!/bin/sh\necho %d\n" % victim.pid,
        )
        assert r.returncode == 5, (
            "cold-relaunch fallback accepted an unreplaced pid: rc=%s out=%r"
            % (r.returncode, r.stdout)
        )
        assert "did NOT replace" in r.stdout
        assert not any(l.startswith("OK:") for l in r.stdout.splitlines()), r.stdout
    finally:
        victim.kill()
        victim.wait()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell guard")
def test_genuine_relaunch_still_passes(tmp_path):
    """Positive control: the guard must not reject a real reload."""
    old = subprocess.Popen(["sleep", "120"])
    new = subprocess.Popen(["sleep", "120"])
    try:
        counter = tmp_path / "n"
        r = _harness(
            tmp_path,
            _extract_guard(),
            "#!/bin/sh\nC=%s\nn=$(cat $C 2>/dev/null || echo 0)\nn=$((n+1))\n"
            "echo $n > $C\nif [ \"$n\" = \"1\" ]; then echo %d; else echo %d; fi\n"
            % (counter, old.pid, new.pid),
        )
        assert r.returncode == 0, (
            "guard rejected a genuine relaunch -- it would be a rubber stamp in "
            "reverse: rc=%s out=%r" % (r.returncode, r.stdout)
        )
        assert re.search(r"OK: running \(pid %d\)" % new.pid, r.stdout), r.stdout
    finally:
        for p in (old, new):
            p.kill()
            p.wait()
