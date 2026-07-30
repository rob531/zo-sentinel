"""Tests for tools/runbook_pin.py.

The load-bearing test is `test_the_2026_07_30_fact_pattern`. It replays the actual
event: the runbook worktree parked at the staged candidate sha ae71dafd while
origin/main was cbd23297. Two tools were ABSENT at that sha and one -- accept_gate,
the tool that decides whether a prod fire is accepted -- was PRESENT AND DIFFERENT.

Every assertion here was seen RED against deliberately wrong implementations, and the
mutation was verified to have LANDED in the file before the result was believed:

  * against an EXISTENCE-ONLY checker (does the file exist at this path?), which is
    what the shadow_decision forwarder does. It flags the two absent tools and is
    blind to accept_gate -- green on the only tool that would have silently answered.
  * against a version that returned PINNED whenever the divergent set was empty,
    which converts "today's delta happens not to touch these five files" into a
    standing safety claim about a worktree that is still at the wrong commit.
  * against a version that mapped an unresolvable HEAD to PINNED, which renders an
    uninspectable worktree as a correct one (R6: unknown is not zero).

AND A SCAR FROM WRITING THIS FILE, kept because it is the same class the tool is about.
The FIRST attempt at the existence-only mutant edited the `elif` comparison away but
left the `else:` fall-through, which still routed present-but-different into
`divergent` -- so the suite went GREEN and, read carelessly, would have "proven" the
negative control passed. A second attempt failed to apply at all (line-ending mismatch
in the replacement) and ALSO went green. Two consecutive green mutant runs, neither of
which had actually tested anything. The mutation is only evidence once you have
asserted that the file CHANGED and that the behaviour you meant to remove is gone --
which the mutation script now does with explicit asserts. A verification step that
cannot fail loudly agrees with you.
"""

import importlib.util
import pathlib
import sys

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "runbook_pin.py"
_spec = importlib.util.spec_from_file_location("runbook_pin", _MOD_PATH)
rp = importlib.util.module_from_spec(_spec)
sys.modules["runbook_pin"] = rp
_spec.loader.exec_module(rp)


HEAD_PARKED = "ae71dafd1295ce03463a520f38678edb8a78a3a3"
MAIN = "cbd232979f3c36c3c9bc35f777bd6ef4a320fdca"


# ---------------------------------------------------------------- the real event
def test_the_2026_07_30_fact_pattern():
    """Absent tools fail loudly; the DIVERGENT one is the one that answers wrong."""
    blobs = {
        # present at both, DIFFERENT -- this is the dangerous one, and it is the
        # tool that decides whether a prod fire is accepted.
        "tools/accept_gate.py": ("70525a30", "b1477e0e"),
        "tools/fire_gate.py": ("9f764624", "9f764624"),
        "tools/sentinel_run_ledger.py": ("d939225b", "d939225b"),
        # landed in #2296, so absent at the parked sha
        "tools/shadow_decision.py": (None, "aaaa1111"),
        # landed in #2410, so absent at the parked sha
        "tools/rollback_anchor_probe.py": (None, "bbbb2222"),
    }
    r = rp.classify(HEAD_PARKED, MAIN, blobs)

    assert r["rc"] == rp.RC_DRIFTED
    assert r["verdict"] == "DRIFTED"
    # The existence-only checker gets these two right...
    assert r["absent"] == ["tools/rollback_anchor_probe.py", "tools/shadow_decision.py"]
    # ...and misses this one entirely. This assertion is the reason the file exists.
    assert r["divergent"] == ["tools/accept_gate.py"]
    assert "tools/accept_gate.py" not in r["absent"]
    assert "SILENTLY" in r["reason"].upper()


# ------------------------------------------------- drift is drift, divergence or not
def test_drifted_with_no_divergent_tools_is_still_drifted():
    """An empty divergent set is a property of today's delta, not of the discipline."""
    blobs = {p: ("same", "same") for p in rp.SENTINEL_TOOLS}
    r = rp.classify(HEAD_PARKED, MAIN, blobs)
    assert r["rc"] == rp.RC_DRIFTED
    assert r["divergent"] == []
    assert len(r["identical"]) == len(rp.SENTINEL_TOOLS)


def test_pinned_when_head_is_target():
    blobs = {p: ("same", "same") for p in rp.SENTINEL_TOOLS}
    r = rp.classify(MAIN, MAIN, blobs)
    assert r["rc"] == rp.RC_PINNED
    assert r["verdict"] == "PINNED"


# ------------------------------------------- the commit is not the file on disk
def test_right_commit_with_an_edited_tool_is_not_pinned():
    """A commit oid describes what was committed. Python executes the DISK.

    This is the tool's own version of the bug it was written about: without this
    branch, a worktree sitting exactly at origin/main with a hand-edited
    accept_gate.py reports PINNED while running code no commit contains.
    """
    blobs = {p: ("same", "same") for p in rp.SENTINEL_TOOLS}
    r = rp.classify(MAIN, MAIN, blobs, dirty=["tools/accept_gate.py"])
    assert r["rc"] == rp.RC_DRIFTED
    assert r["rc"] != rp.RC_PINNED
    assert r["dirty"] == ["tools/accept_gate.py"]
    assert "UNCOMMITTED" in r["reason"].upper()


def test_dirty_is_reported_even_when_the_commit_also_drifted():
    blobs = {p: ("a", "b") for p in rp.SENTINEL_TOOLS}
    r = rp.classify(HEAD_PARKED, MAIN, blobs, dirty=["tools/fire_gate.py"])
    assert r["rc"] == rp.RC_DRIFTED
    assert r["dirty"] == ["tools/fire_gate.py"]


@pytest.mark.parametrize("out", [
    # THE SHAPE THE REAL RUNNER ACTUALLY PRODUCES. `_git` calls .strip(), which eats
    # porcelain's leading status space -- so a single modified file arrives with NO
    # leading column at all. The first version of this test fed the unstripped form
    # below and passed against a parser that was, live, reporting an edited
    # accept_gate.py as clean. This case was captured from a real run.
    "M tools/accept_gate.py",
    # unstripped, as git emits it before .strip()
    " M tools/accept_gate.py",
    # staged + unstaged
    "MM tools/accept_gate.py",
    # windows separators
    " M tools\\accept_gate.py",
    # quoted path
    ' M "tools/accept_gate.py"',
])
def test_dirty_parser_survives_every_porcelain_shape(tmp_path, out):
    """Parse real `git status --porcelain` shapes, not an idealised one."""
    class G:
        def __call__(self, _wt, *args):
            return (0, out) if args[0] == "status" else (1, "")
    assert rp._dirty(str(tmp_path), G()) == ["tools/accept_gate.py"]


def test_dirty_parser_ignores_non_sentinel_paths(tmp_path):
    class G:
        def __call__(self, _wt, *args):
            if args[0] == "status":
                return 0, "?? tools/scratch.py\nM tools/fire_gate.py"
            return 1, ""
    got = rp._dirty(str(tmp_path), G())
    assert got == ["tools/fire_gate.py"]
    assert "tools/scratch.py" not in got


def test_tool_removed_upstream_counts_as_divergent():
    """Present here, gone at target: still 'what runs is not what should'."""
    r = rp.classify(HEAD_PARKED, MAIN, {"tools/accept_gate.py": ("70525a30", None)})
    assert r["rc"] == rp.RC_DRIFTED
    assert r["divergent"] == ["tools/accept_gate.py"]


# ------------------------------------------------------------- unknown is not zero
@pytest.mark.parametrize("head,target", [(None, MAIN), (MAIN, None), (None, None)])
def test_unresolvable_commit_is_unknown_never_pinned(head, target):
    r = rp.classify(head, target, {})
    assert r["rc"] == rp.RC_UNKNOWN
    assert r["rc"] != rp.RC_PINNED
    assert r["verdict"] == "UNKNOWN"


def test_missing_worktree_path_is_unknown(tmp_path):
    r = rp.inspect(worktree=str(tmp_path / "nope"))
    assert r["rc"] == rp.RC_UNKNOWN
    assert "does not exist" in r["reason"]


# --------------------------------------------------------------- recovery, and idempotent
class FakeGit:
    """Minimal git stand-in: HEAD moves to `target` when `checkout --detach` runs."""

    def __init__(self, head, target):
        self.head, self.target = head, target
        self.checkouts = 0

    def __call__(self, _wt, *args):
        if args[0] == "fetch":
            return 0, ""
        if args[0] == "checkout":
            self.checkouts += 1
            self.head = self.target
            return 0, ""
        if args[0] == "rev-parse":
            ref = args[1]
            if ref == "HEAD":
                return 0, self.head
            if ref == "origin/main":
                return 0, self.target
            if ":" in ref:
                return 0, "blob-" + ref.split(":", 1)[1]
        return 1, ""


def test_heal_converges_a_drifted_worktree(tmp_path):
    g = FakeGit(HEAD_PARKED, MAIN)
    assert rp.inspect(str(tmp_path), runner=g)["rc"] == rp.RC_DRIFTED
    r = rp.heal(str(tmp_path), runner=g)
    assert r["rc"] == rp.RC_PINNED
    assert r["head_before_heal"] == HEAD_PARKED
    assert r["head"] == MAIN


def test_heal_is_idempotent_on_an_already_pinned_worktree(tmp_path):
    g = FakeGit(MAIN, MAIN)
    first = rp.heal(str(tmp_path), runner=g)
    second = rp.heal(str(tmp_path), runner=g)
    assert first["rc"] == second["rc"] == rp.RC_PINNED
    assert second["head"] == MAIN


def test_heal_on_a_missing_worktree_stays_unknown(tmp_path):
    r = rp.heal(str(tmp_path / "nope"))
    assert r["rc"] == rp.RC_UNKNOWN
    assert r["healed"] is False


import os as _os


# --------------------------------------------------------------- FU-202: resolution
#
# The defect these cover is not "the classifier was wrong" -- the classifier was
# right about a tree NOBODY WAS RUNNING FROM. `--worktree` defaulted to the
# hardcoded shared `_runbook`, so after lane isolation (#2416)
# `cd <lane> && runbook_pin --heal` measured the wrong tree, refused (shared) and
# returned rc=1 -- as the step immediately before `accept_gate` in the prod
# one-click. R1 in miniature: naming HOW the artifact was resolved is the check.


def _runner_toplevel(top, rc=0):
    """Fake git that answers `rev-parse --show-toplevel` and nothing else."""
    def run(worktree, *args):
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return rc, top
        return 1, ""
    return run


def test_explicit_worktree_always_wins_so_existing_callers_are_unchanged():
    path, basis = rp.resolve_worktree(
        explicit=r"D:\zo\_lanes\somelane",
        cwd=r"D:\elsewhere",
        runner=_runner_toplevel(r"D:\zo\_runbook"),
    )
    assert path == r"D:\zo\_lanes\somelane"
    assert basis == "explicit --worktree"


def test_default_is_the_worktree_enclosing_the_cwd_not_the_hardcoded_shared_tree():
    path, basis = rp.resolve_worktree(
        cwd=r"D:\zo\_lanes\prod-fire",
        runner=_runner_toplevel(r"D:\zo\_lanes\prod-fire"),
    )
    assert path == _os.path.normpath(r"D:\zo\_lanes\prod-fire")
    assert path != rp.DEFAULT_WORKTREE
    assert "enclosing git worktree" in basis


def test_falls_back_to_the_constant_only_when_cwd_is_not_in_a_worktree():
    path, basis = rp.resolve_worktree(
        cwd="C:/", runner=_runner_toplevel("", rc=128))
    assert path == rp.DEFAULT_WORKTREE
    assert "fallback" in basis
    assert "not inside a git worktree" in basis


def test_the_basis_is_always_populated_so_a_resolution_can_be_named():
    # R1: a resolution you cannot name is one you have not made. Every branch
    # must return a non-empty basis, including the fallback.
    for kwargs in (
        {"explicit": r"D:\x"},
        {"cwd": r"D:\zo\_lanes\l", "runner": _runner_toplevel(r"D:\zo\_lanes\l")},
        {"cwd": "C:/", "runner": _runner_toplevel("", rc=128)},
    ):
        _, basis = rp.resolve_worktree(**kwargs)
        assert basis and isinstance(basis, str)
