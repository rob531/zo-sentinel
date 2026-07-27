"""FU-121 -- the mesh's own reload protocol could not reload half its daemons.

Found 2026-07-27 while attempting the sanctioned repair for FU-115:

    $ bash tools/reload_daemon.sh proposed_to_pending_promoter
    ERROR: proposed_to_pending_promoter.py not found in
           /home/workspace/zo_sentinel or /home/workspace/zo_mesh

The daemon was demonstrably alive -- pid 16923,
`python3 -m zo_sentinel.promoters.proposed_to_pending_promoter`. It simply runs
as a MODULE, and the resolver looked only for a top-level `<name>.py` in two
flat directories. So the whole `zo_sentinel.promoters.*` namespace -- and every
future package-structured daemon, which is the direction SOA is moving
everything -- was unreloadable by the protocol built to replace hand-rolled
kill/restart, and the standing act-authority "reload an idle daemon" was
unexecutable for exactly the class of daemon we are building more of.

These tests drive the REAL script under RELOAD_DAEMON_RESOLVE_ONLY=1, which
resolves the target and exits without signalling anything. Resolution is the
half that was broken, and it is the half that can be checked without a live
process tree.
"""
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RELOAD = REPO / "tools" / "reload_daemon.sh"
WRAPPER = REPO / "tools" / "daemon_wrapper.sh"


def _tree(tmp_path):
    """A miniature of the live layout: a flat file daemon and a packaged one."""
    sentinel = tmp_path / "zo_sentinel"
    mesh = tmp_path / "zo_mesh"
    (sentinel / "zo_sentinel" / "promoters").mkdir(parents=True)
    mesh.mkdir(parents=True)

    # flat, top-level daemon -- the shape that always worked
    (sentinel / "goose_runner.py").write_text("# flat daemon\n")
    (mesh / "liveness_probe.py").write_text("# flat mesh daemon\n")

    # packaged, module-invoked daemon -- the shape FU-121 could not see
    (sentinel / "zo_sentinel" / "__init__.py").write_text("")
    (sentinel / "zo_sentinel" / "promoters" / "__init__.py").write_text("")
    (sentinel / "zo_sentinel" / "promoters" /
     "proposed_to_pending_promoter.py").write_text("# module daemon\n")
    return sentinel, mesh


def _matches(pattern, cmdline):
    """True iff `pgrep -f <pattern>` would match `cmdline`.

    The pattern is passed through argv, never interpolated into a shell string
    -- an early draft used repr() and the doubled backslash made a CORRECT
    pattern look broken. A test harness that mangles its own input reports a
    defect in itself as a defect in the code.
    """
    return subprocess.run(
        ["bash", "-c", 'echo "$1" | grep -Eq "$2"', "_", cmdline, pattern],
        capture_output=True, text=True).returncode == 0


def _resolve(tmp_path, name):
    """Run the real script in resolve-only mode against a fake tree."""
    sentinel, mesh = _tree(tmp_path)
    patched = RELOAD.read_text(encoding="utf-8")
    patched = patched.replace('MESH="/home/workspace/zo_mesh"', f'MESH="{mesh}"')
    patched = patched.replace('SENTINEL="/home/workspace/zo_sentinel"',
                              f'SENTINEL="{sentinel}"')
    script = tmp_path / "reload_daemon.sh"
    script.write_text(patched, encoding="utf-8")

    env = dict(os.environ, RELOAD_DAEMON_RESOLVE_ONLY="1")
    p = subprocess.run(["bash", str(script), name], env=env,
                       capture_output=True, text=True, timeout=60)
    fields = dict(
        line.split("=", 1) for line in p.stdout.strip().splitlines() if "=" in line
    )
    return p, fields


# --- the defect ------------------------------------------------------------

def test_a_module_invoked_daemon_resolves(tmp_path):
    """The live failure. `python3 -m zo_sentinel.promoters.<name>` has no
    `<name>.py` anywhere in its cmdline, which is why the old resolver and the
    old pgrep pattern were both blind to it."""
    p, f = _resolve(tmp_path, "proposed_to_pending_promoter")

    assert p.returncode == 0, f"resolution must succeed:\n{p.stdout}\n{p.stderr}"
    assert f["mode"] == "module"
    assert f["module"] == "zo_sentinel.promoters.proposed_to_pending_promoter", (
        "the dotted path must match how the process ACTUALLY runs -- it is what "
        "python -m will be handed on relaunch"
    )
    assert f["run_cwd"].endswith("zo_sentinel")
    assert "-m " in f["child_pat"], "the child pattern must match the -m form"
    assert ".py" not in f["child_pat"]


def test_the_module_child_pattern_matches_the_real_live_cmdline(tmp_path):
    """Guard the regex against the verbatim cmdline of pid 16923, so a pattern
    that resolves but never matches cannot pass."""
    _p, f = _resolve(tmp_path, "proposed_to_pending_promoter")
    live = "python3 -m zo_sentinel.promoters.proposed_to_pending_promoter"

    assert _matches(f["child_pat"], live), (
        f"child_pat {f['child_pat']!r} does not match the live cmdline {live!r}")


def test_the_pattern_does_not_match_a_sibling_module(tmp_path):
    """`...promoters.proposed_to_pending_promoter_v2` is a different daemon."""
    _p, f = _resolve(tmp_path, "proposed_to_pending_promoter")
    sibling = "python3 -m zo_sentinel.promoters.proposed_to_pending_promoter_v2"

    assert not _matches(f["child_pat"], sibling), \
        "a reload must never kill a sibling daemon"


# --- everything that worked before must still work exactly as before -------

@pytest.mark.parametrize("name", ["goose_runner", "liveness_probe"])
def test_flat_file_daemons_are_unchanged(tmp_path, name):
    p, f = _resolve(tmp_path, name)
    assert p.returncode == 0
    assert f["mode"] == "file"
    assert f["script"].endswith(f"{name}.py")
    assert f["child_pat"] == f"python.*{name}\\.py", (
        "the file path must be byte-identical to the pre-FU-121 pattern")


def test_a_dot_py_suffix_is_still_stripped(tmp_path):
    p, f = _resolve(tmp_path, "goose_runner.py")
    assert p.returncode == 0 and f["mode"] == "file"


def test_a_genuinely_absent_daemon_still_fails_loudly(tmp_path):
    """The one merciful part of the original bug was that it was loud. Widening
    the resolver must not turn 'not found' into a silent success."""
    p, _f = _resolve(tmp_path, "no_such_daemon_anywhere")
    assert p.returncode == 2
    assert "ERROR" in p.stdout + p.stderr


# --- the wrapper half ------------------------------------------------------

def test_the_wrapper_accepts_a_module_spec(tmp_path):
    """Without this, resolution succeeds and cold relaunch still cannot start a
    module daemon -- resolution alone would be a fix that looks like a fix."""
    src = WRAPPER.read_text(encoding="utf-8")
    assert '-m "$MODULE"' in src or "python3 -m" in src

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "sentinel_test_daemon.py").write_text(
        'print("MODULE DAEMON RAN"); raise SystemExit(0)\n')
    logs = tmp_path / "logs"
    logs.mkdir()

    patched = src.replace('WRAPPER_LOG="/home/workspace/logs/wrapper_${NAME}.log"',
                          f'WRAPPER_LOG="{logs}/wrapper_${{NAME}}.log"')
    patched = patched.replace(
        'RELOAD_MARKER="/home/workspace/zo_mesh/.reload_${NAME}"',
        f'RELOAD_MARKER="{tmp_path}/.reload_${{NAME}}"')
    w = tmp_path / "daemon_wrapper.sh"
    w.write_text(patched, encoding="utf-8")

    # rc=0 with no reload marker means "clean exit, do not respawn", so this
    # terminates rather than looping.
    p = subprocess.run(["bash", str(w), "sentinel_test_daemon",
                        "-m", "pkg.sentinel_test_daemon"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert "MODULE DAEMON RAN" in p.stdout, (
        f"wrapper failed to run the module:\nout={p.stdout}\nerr={p.stderr}")
    assert p.returncode == 0


def test_the_wrapper_still_rejects_a_missing_script_path(tmp_path):
    """File mode keeps its guard; -m must not become a way to skip validation."""
    p = subprocess.run(["bash", str(WRAPPER), "x", "/nonexistent/path.py"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2
    assert "not found" in p.stderr


def test_the_wrapper_rejects_a_bare_dash_m(tmp_path):
    p = subprocess.run(["bash", str(WRAPPER), "x", "-m"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2
