"""FU-151 -- every flyctl caller reaches the ONE credential path.

The test that matters here is `test_every_flyctl_proxy_caller_hydrates`. FU-137
diagnosed the 2026-07-28 flyctl outage correctly and shipped a correct fix into
exactly ONE caller; the identical outage then recurred the next day in a
different lane, because nothing asserted that the OTHER callers had been
patched. A helper the caller was never pointed at is an uncalled helper. That
test is the assertion which was missing, and it fires on the NEXT flyctl caller
somebody adds without having read any of this.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from fly_token import (  # noqa: E402
    hydrate_fly_token,
    proxy_error_detail,
    port_open,
    wait_for_proxy,
)


# --------------------------------------------------------------------------
# hydrate_fly_token contract
# --------------------------------------------------------------------------

def test_preset_token_is_never_overwritten():
    env = {"FLY_API_TOKEN": "preset"}
    hydrated, note = hydrate_fly_token(env=env)
    assert hydrated is False
    assert env["FLY_API_TOKEN"] == "preset"
    assert "already present" in note


def test_missing_vault_is_non_fatal_and_names_the_path():
    env = {}
    hydrated, note = hydrate_fly_token(env=env, fetch_path=r"Z:\definitely\not\here.py")
    assert hydrated is False
    assert "not found" in note
    assert "FLY_API_TOKEN" not in env


def test_vault_hydrates_and_strips_whitespace():
    class R:
        returncode, stdout = 0, "  fm2_realtoken  \n"

    env = {}
    hydrated, note = hydrate_fly_token(env=env, fetch_path=str(Path(__file__)),
                                       runner=lambda cmd: R())
    assert hydrated is True
    assert env["FLY_API_TOKEN"] == "fm2_realtoken"
    assert "len=13" in note


def test_vault_failure_is_non_fatal_and_leaves_env_clean():
    """A credential helper that can itself take down the lane is a worse bargain
    than the bug it fixes."""
    def boom(cmd):
        raise OSError("vault down")

    env = {}
    hydrated, note = hydrate_fly_token(env=env, fetch_path=str(Path(__file__)),
                                       runner=boom)
    assert hydrated is False
    assert "non-fatal" in note
    assert "FLY_API_TOKEN" not in env


def test_vault_nonzero_rc_does_not_export_empty_token():
    class R:
        returncode, stdout = 3, ""

    env = {}
    hydrated, _ = hydrate_fly_token(env=env, fetch_path=str(Path(__file__)),
                                    runner=lambda cmd: R())
    assert hydrated is False
    assert "FLY_API_TOKEN" not in env


# --------------------------------------------------------------------------
# the diagnostic half (FU-133 propagated)
# --------------------------------------------------------------------------

class _Dead:
    def poll(self):
        return 1


def test_error_detail_quotes_flyctls_own_last_line():
    with tempfile.TemporaryDirectory() as d:
        err = Path(d) / "e.err"
        err.write_text("connecting...\nError: no access token available\n")
        detail = proxy_error_detail(_Dead(), err)
    assert "no access token available" in detail
    assert "exited 1" in detail


def test_error_detail_survives_an_absent_stderr_file():
    assert isinstance(proxy_error_detail(_Dead(), Path("Z:/nope/none.err")), str)


def test_wait_for_proxy_does_not_wait_out_a_corpse():
    """A flyctl that has already exited is not waited out for the remaining
    clock -- waiting out a corpse is theatre, and it is what turned a 0-second
    auth failure into a 60-second timeout report."""
    with tempfile.TemporaryDirectory() as d:
        err = Path(d) / "e.err"
        err.write_text("Error: no access token available\n")
        with pytest.raises(RuntimeError) as ei:
            wait_for_proxy(_Dead(), 1, err, timeout_s=60, poll_s=0)
    assert "no access token available" in str(ei.value)


def test_port_open_is_false_for_a_dead_port():
    assert port_open(1, timeout=1) is False


# --------------------------------------------------------------------------
# THE regression test: the fix must reach every actor, not just one
# --------------------------------------------------------------------------

_HYDRATION_MARKERS = ("hydrate_fly_token", "fly_token", "FLY_API_TOKEN")

# fly_token.py IS the credential path; tests assert about it rather than use it.
_EXEMPT = {"tools/fly_token.py"}


def _spawns_fly_proxy(src: str) -> bool:
    """True if this source literally spawns `flyctl proxy`."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip() in ("flyctl", "fly"):
                return True
    return False


def _candidate_files():
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv",
                                "venv", "services", "directives", "tests"}]
        for f in files:
            if f.endswith(".py"):
                out.append(Path(root) / f)
    return out


def test_every_flyctl_proxy_caller_hydrates():
    """Any module that spawns flyctl must reach the shared credential path.

    This is the assertion FU-137 lacked. It is deliberately source-level rather
    than behavioural: the failure mode being pinned is *a caller nobody updated*,
    which no runtime test can see because that caller is never exercised in CI.
    """
    offenders = []
    for path in _candidate_files():
        rel = path.relative_to(REPO).as_posix()
        if rel in _EXEMPT:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "flyctl" not in src or "proxy" not in src:
            continue
        if not _spawns_fly_proxy(src):
            continue
        if not any(m in src for m in _HYDRATION_MARKERS):
            offenders.append(rel)
    assert not offenders, (
        "these modules spawn flyctl without reaching the shared credential path "
        "(tools/fly_token.py) -- FU-151: a helper the caller was never pointed at "
        "is an uncalled helper: " + ", ".join(sorted(offenders)))


def test_the_three_known_callers_are_wired():
    """Named explicitly so the scan above cannot pass by finding nothing."""
    for rel in ("tools/rescore/weekly_rescore.py",
                "tools/rescore/delta_report.py",
                "tools/canonical/materialize_canonical_family.py"):
        src = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        assert "fly_token" in src, f"{rel} does not import the shared credential path"


def test_fly_token_selftest_passes_standalone():
    """The module's own ACCEPTANCE clause actually executes (FU-031/FU-142:
    a self-test that never runs is not a self-test)."""
    p = subprocess.run([sys.executable, str(REPO / "tools" / "fly_token.py")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SELFTEST OK" in p.stdout
