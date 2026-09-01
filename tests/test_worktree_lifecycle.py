"""FU-157 -- the disposable-worktree lifecycle must have exactly ONE definition.

WHY THIS TEST EXISTS
--------------------
`Reset-DisposableWorktree` was copied into both ops/host/deploy_prod.ps1 and
ops/host/verify_candidate.ps1. The copies then DIVERGED, in the direction that
matters least and hurts most:

  verify_candidate.ps1 (the OBSERVER) learned that an EMPTY leftover directory
  is not an orphan -- measured, `git worktree add` at an empty dir succeeds.

  deploy_prod.ps1 (the ACTOR, the one the chairman fires) never learned it, and
  calls the helper with -MustSucceed $true BEFORE creating its worktree. So an
  empty leftover -- a state observed live on 2026-07-28 at D:\\zo\\_prod_dryrun
  -- would have aborted the fire with "a process is holding a file open; find
  it before firing", over a condition measured to block nothing.

A duplicated safeguard does not drift randomly. It drifts so that the fix lands
in the copy that watches rather than the copy that acts, because the watcher is
the one you are looking at when you learn the lesson. These assertions make the
duplication itself the failure, not the divergence -- you cannot diverge from a
definition you do not have.

Every assertion below was seen RED against the pre-fix tree (919a02f6).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "ops" / "host" / "worktree_lifecycle.ps1"

# The two scripts that manage a disposable worktree. deploy_prod.ps1 is the
# fire path; verify_candidate.ps1 is the gate the sentinel runs every 3h.
CALLERS = [
    REPO / "ops" / "host" / "deploy_prod.ps1",
    REPO / "ops" / "host" / "verify_candidate.ps1",
]

DEF_RE = re.compile(r"^\s*function\s+Reset-DisposableWorktree\b", re.MULTILINE | re.IGNORECASE)
GIT_BE_RE = re.compile(r"^\s*function\s+Git-BestEffort\b", re.MULTILINE | re.IGNORECASE)
DOTSOURCE_RE = re.compile(
    r"^\s*\.\s+\(Join-Path\s+\$PSScriptRoot\s+[\"']worktree_lifecycle\.ps1[\"']\)",
    re.MULTILINE | re.IGNORECASE,
)


def _read(p: Path) -> str:
    assert p.is_file(), f"expected to exist: {p.relative_to(REPO)}"
    return p.read_text(encoding="utf-8-sig")


# --------------------------------------------------------------- single source


def test_helper_file_exists():
    assert HELPER.is_file(), (
        "ops/host/worktree_lifecycle.ps1 is the single source of the worktree "
        "lifecycle. Without it every caller keeps its own copy and they diverge."
    )


def test_helper_defines_each_function_exactly_once():
    src = _read(HELPER)
    assert len(DEF_RE.findall(src)) == 1
    assert len(GIT_BE_RE.findall(src)) == 1


@pytest.mark.parametrize("caller", CALLERS, ids=lambda p: p.name)
def test_caller_does_not_redefine_the_helper(caller: Path):
    """The assertion that would have caught FU-157 on the day the copies split."""
    src = _read(caller)
    hits = DEF_RE.findall(src)
    assert not hits, (
        f"{caller.name} defines its own Reset-DisposableWorktree. Two definitions "
        "cannot be kept in agreement by intention -- FU-157 is what happens when "
        "they drift. Dot-source ops/host/worktree_lifecycle.ps1 instead."
    )


@pytest.mark.parametrize("caller", CALLERS, ids=lambda p: p.name)
def test_caller_dot_sources_the_helper_beside_itself(caller: Path):
    """$PSScriptRoot, never $WorktreePath.

    The helper belongs to the RUNBOOK, not to the deployed tree: the sha being
    shipped generally predates it (7fc39201 does). Resolving it inside the
    checked-out worktree would make the guard vanish exactly when shipping an
    older tree -- the same trap tools/accept_gate.py had to avoid.
    """
    src = _read(caller)
    assert DOTSOURCE_RE.search(src), (
        f"{caller.name} must dot-source the helper from $PSScriptRoot: "
        '. (Join-Path $PSScriptRoot "worktree_lifecycle.ps1")'
    )


@pytest.mark.parametrize("caller", CALLERS, ids=lambda p: p.name)
def test_missing_helper_is_fatal_not_a_silent_skip(caller: Path):
    """An absent guard must not read as a passing one.

    If the dot-source target is missing, PowerShell throws under
    ErrorActionPreference=Stop -- but only if nothing swallows it. Require an
    explicit Test-Path guard that names the file and dies, so the failure says
    what is wrong instead of 'Reset-DisposableWorktree is not recognized'.
    """
    src = _read(caller)
    guard = re.search(
        r"if\s*\(\s*-not\s*\(\s*Test-Path\s+\$_?[A-Za-z]*[Ll]ifecycle\w*\s*\)\s*\)",
        src,
    )
    assert guard, (
        f"{caller.name} must fail loudly when worktree_lifecycle.ps1 is absent. "
        "A guard that silently is not there is a guard that reports PASS."
    )


# ------------------------------------------------- the divergence that bit us


def test_empty_leftover_is_treated_as_cleared():
    """MEASURED 2026-07-28 (twice, independently):

        worktree add --detach at an EXISTING EMPTY dir  -> rc=0, full checkout
        worktree add --detach at a NON-EMPTY dir        -> rc=128 'already exists'

    So an empty leftover blocks nothing and must never be escalated. This is
    the branch deploy_prod.ps1 was missing.
    """
    src = _read(HELPER)
    assert re.search(r"\$left\s*-eq\s*0", src), (
        "the single source must keep the empty-leftover branch -- without it the "
        "fire path halts on a condition measured to be harmless"
    )
    idx = src.index("$left -eq 0")
    tail = src[idx : idx + 800]
    assert "return $true" in tail, "an empty leftover must return cleared, not fall through to the retry/Die path"


def test_non_empty_leftover_still_escalates():
    """The other half: a real orphan must still be able to stop the caller.

    An assertion that only proves the permissive branch would pass just as well
    against a helper that never fails at all.
    """
    src = _read(HELPER)
    assert re.search(r"if\s*\(\s*\$MustSucceed\s*\)", src)
    assert "exit 1" in src, "MustSucceed must be able to terminate the caller"


def test_must_succeed_and_fatal_message_are_parameters():
    """The ONE thing that legitimately differs by call site.

    Fatal before creating the worktree (an unpinnable path ships an unknown
    tree); loud-but-not-fatal after a deploy has run (the acceptance gate is
    the verdict, and leftover files must not report a good ship as a failure).
    Parameterise that difference -- forking the file to express it is precisely
    how the empty-dir fix failed to reach the actor.
    """
    src = _read(HELPER)
    assert re.search(r"\[bool\]\$MustSucceed", src)
    assert re.search(r"\[string\]\$FatalMessage", src)


def test_deploy_prod_keeps_both_call_site_semantics():
    src = _read(REPO / "ops" / "host" / "deploy_prod.ps1")
    calls = re.findall(r"Reset-DisposableWorktree[^\r\n]*", src)
    calls = [c for c in calls if "MustSucceed" in c]
    assert any("$true" in c for c in calls), "pre-create call must be fatal"
    assert any("$false" in c for c in calls), "post-deploy teardown must NOT be fatal"
