"""Safety + idempotency contract for ops/host/verify_candidate.ps1.

prod-drift-sentinel runs this script UNATTENDED every 3h under Phase-1 rules,
which forbid it from touching prod. The invariants below are the ones that make
that safe, and the ones whose absence has already cost a run:

  * no prod write path (`flyctl deploy`, `alembic upgrade`) may appear in the
    staging script -- Phase 1 stages, it never pushes;
  * the disposable worktree must be healed BEFORE creation and VERIFIED gone
    AFTER teardown (2026-07-27: `git worktree remove --force` pruned the
    metadata but left 3,466 files behind, and the next run died on
    "already exists" -- a state neither `remove` nor `prune` can fix);
  * the verdict artifact must be rescued out of the worktree before teardown
    destroys it, or the stage has no evidence behind it;
  * dirtiness must be snapshotted BEFORE the gates run, because the gates
    themselves write tracked artifacts.

These are text assertions on purpose: PowerShell is not importable from pytest,
and the alternative -- running the script -- would create worktrees in CI. Same
pattern as tests/test_dockerfile_copy_covers_active_services.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "ops" / "host" / "verify_candidate.ps1"
# FU-157: Reset-DisposableWorktree was copied into BOTH this script and
# deploy_prod.ps1, and the copies diverged -- the empty-leftover branch below
# reached the observer and never reached the actor, so the fire path halted on
# a harmless condition. One definition now. Assertions about the HELPER read
# the helper; assertions about CALL ORDERING still read the script.
LIFECYCLE = REPO_ROOT / "ops" / "host" / "worktree_lifecycle.ps1"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT.is_file(), f"missing staging script: {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def helper_text(script_text: str) -> str:
    """Resolve the helper the way PowerShell will -- through the dot-source.

    $PSScriptRoot, never $WorktreePath: the helper belongs to the runbook, not
    to the tree being gated, which generally predates it.
    """
    assert re.search(
        r"\.\s+\(Join-Path\s+\$PSScriptRoot\s+[\"']worktree_lifecycle\.ps1[\"']\)",
        script_text,
    ), f"{SCRIPT.name} must dot-source ops/host/worktree_lifecycle.ps1"
    assert LIFECYCLE.is_file(), f"missing worktree lifecycle helper: {LIFECYCLE}"
    return LIFECYCLE.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Strip comments and the leading comment-based help block.

    The script's own docs quote the forbidden commands while explaining why they
    are forbidden; a naive substring search would fail on its own rationale.
    """
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("<#"):
            in_block = True
        if in_block:
            if "#>" in line:
                in_block = False
            continue
        if line.startswith("#"):
            continue
        out.append(raw.split("#", 1)[0] if "#" in raw else raw)
    return out


# ----------------------------------------------------------------- safety


@pytest.mark.parametrize(
    "forbidden",
    [
        "flyctl deploy",
        "fly deploy",
        "alembic upgrade",
    ],
)
def test_staging_script_contains_no_prod_write(script_text: str, forbidden: str) -> None:
    """Phase 1 stages a deploy; it must be incapable of firing one."""
    offenders = [ln for ln in _code_lines(script_text) if forbidden in ln.lower()]
    assert not offenders, (
        f"ops/host/verify_candidate.ps1 must never invoke '{forbidden}' -- it runs "
        f"unattended under Phase-1 rules. Offending line(s): {offenders}. "
        "The FIRE path lives in ops/host/deploy_prod.ps1 and is human-invoked."
    )


def test_script_requires_a_full_sha(script_text: str) -> None:
    """A short sha makes the gated identity ambiguous (see #2063)."""
    assert re.search(r"\^\[0-9a-f\]\{40\}\$", script_text), (
        "verify_candidate.ps1 must validate -Sha against ^[0-9a-f]{40}$ so the "
        "tree that was gated is unambiguous."
    )


# ------------------------------------------------------- idempotency contract


def test_worktree_is_healed_before_creation(script_text: str, helper_text: str) -> None:
    """A leftover directory with pruned metadata is unremovable by git alone."""
    code = "\n".join(_code_lines(script_text))
    assert "Reset-DisposableWorktree" in code, "expected a single reset helper"
    add_idx = code.find("worktree add")
    reset_idx = code.find("Reset-DisposableWorktree -RepoPath")
    assert reset_idx != -1 and add_idx != -1, "expected both a reset call and a worktree add"
    assert reset_idx < add_idx, (
        "the disposable worktree must be RESET before `git worktree add`, or a "
        "leftover directory from a prior run makes add fail with 'already exists'."
    )
    assert "Remove-Item" in "\n".join(_code_lines(helper_text)), (
        "the reset must fall back to deleting a surviving directory -- "
        "`git worktree remove` + `prune` cannot clear a path whose metadata is gone."
    )


def test_teardown_is_verified_not_assumed(script_text: str, helper_text: str) -> None:
    """`worktree remove` succeeding is not proof the path is gone.

    Ordering matters and a bare `"Test-Path $Path" in code` will not catch it:
    the helper legitimately Test-Paths the directory BEFORE removal too (the
    orphan heal). Only a check that follows the Remove-Item fallback proves the
    teardown, so assert on position, not presence.
    """
    code = "\n".join(_code_lines(script_text))
    helper = "\n".join(_code_lines(helper_text))
    # Anchor INSIDE the retry loop. rfind() would land on the operator-guidance
    # message at the end of the helper, which quotes the same command as prose --
    # a matcher fooled by text shaped like code reports a missing guard that is
    # right there. Anchoring on the loop also asserts more: removal AND proof
    # both live inside the bounded retry.
    loop_idx = helper.find("for ($i = 1; $i -le $Attempts")
    assert loop_idx != -1, "expected a bounded retry loop"
    remove_idx = helper.find("Remove-Item -LiteralPath $Path", loop_idx)
    assert remove_idx != -1, "expected a Remove-Item fallback for a surviving directory"
    proof_idx = helper.find("Test-Path $Path", remove_idx)
    assert proof_idx != -1, (
        "the reset helper must Test-Path the worktree AFTER removing it -- the "
        "14:00Z run of 2026-07-27 recorded worktrees_cleaned=true over 3,466 "
        "surviving files, and the next run died on 'already exists'."
    )
    assert code.count("MustSucceed") + helper.count("MustSucceed") >= 2, (
        "teardown must be able to fail loudly (MustSucceed) rather than leave an "
        "orphan for the next run to inherit."
    )
    assert "finally {" in code, "teardown must run in a finally block"
    assert code.rfind("Reset-DisposableWorktree") > code.rfind("finally {"), (
        "the reset must be called from the finally block so a failed gate run "
        "still tears its worktree down."
    )


def test_verdict_evidence_is_rescued_before_teardown(script_text: str) -> None:
    """The verdict lives inside the worktree the next step destroys."""
    code = "\n".join(_code_lines(script_text))
    assert "deploy_candidate_verdict.json" in code, (
        "the script must locate the verifier's verdict artifact"
    )
    copy_idx = code.find("Copy-Item $src")
    finally_idx = code.rfind("finally {")
    assert copy_idx != -1, "the verdict must be copied out"
    assert copy_idx < finally_idx, (
        "the verdict must be rescued BEFORE the finally block tears the worktree "
        "down, otherwise every stage's evidence is deleted seconds after it is made."
    )
    assert "verdict_latest.json" in code, "expected a stable pointer to the newest verdict"


def test_dirtiness_is_snapshotted_before_gates_run(script_text: str) -> None:
    """The gates write tracked artifacts; measuring after measures the gates."""
    code = "\n".join(_code_lines(script_text))
    status_idx = code.find("git status --porcelain")
    gates_idx = code.find("verify_deploy_candidate.py")
    assert status_idx != -1 and gates_idx != -1
    assert status_idx < gates_idx, (
        "dirtiness must be snapshotted BEFORE tools/verify_deploy_candidate.py "
        "runs -- the gates themselves write artifacts/ci_smoke_junit.xml."
    )
    assert "$Strict" in code, (
        "a dirty pre-gate tree must be able to FAIL the run, not just warn: a "
        "PASS that describes disk instead of the commit is not a PASS."
    )


def test_teardown_retries_before_it_fails(helper_text: str) -> None:
    """A closing file handle is not a wedged process -- distinguish them.

    The first cut of Reset-DisposableWorktree died on the FIRST failed removal
    and immediately fired a false alarm: 6 of 3,298 files were still held
    moments after the gates exited, and a retry 3s later cleared them on the
    first attempt. Both failure modes are real and they pull in opposite
    directions, so the helper must do BOTH:

      * retry with backoff, or a normal teardown pages the chairman;
      * still fail loudly at the end, or a wedge becomes a silent orphan that
        breaks the next run (the defect this whole script exists to fix).
    """
    code = "\n".join(_code_lines(helper_text))
    assert "$Attempts" in code, (
        "teardown must be bounded-retry, not one-shot -- a handle that is still "
        "closing is a timing fact, not a fault."
    )
    assert "Start-Sleep" in code, "retries must back off rather than spin"

    loop_idx = code.find("for ($i = 1; $i -le $Attempts")
    assert loop_idx != -1, "expected a bounded retry loop over $Attempts"

    remove_idx = code.find("Remove-Item -LiteralPath $Path", loop_idx)
    assert remove_idx != -1, "the removal must happen INSIDE the retry loop"

    # The loud failure must survive the retries -- retrying must not become a
    # way of quietly giving up.
    # The helper is dot-sourced, so it must not depend on a Die() the CALLER
    # happens to define -- it terminates on its own. Assert the terminating
    # exit, positioned after the loop, rather than the caller's helper name.
    guard_idx = code.rfind("if ($MustSucceed)")
    exit_idx = code.rfind("exit 1")
    assert guard_idx > loop_idx, (
        "after the retries are exhausted the helper must still reach its "
        "-MustSucceed branch: bounded patience, not unbounded tolerance."
    )
    assert exit_idx > guard_idx, (
        "the -MustSucceed branch must TERMINATE the caller, not merely warn"
    )


def test_empty_leftover_directory_is_not_fatal(helper_text: str) -> None:
    """An EMPTY leftover worktree dir must be treated as cleared, not fatal.

    Scar 2026-07-28: the 04:47Z prod-drift-sentinel run could not gate its
    candidate at all. Reset-DisposableWorktree found D:\\zo\\_prod_dryrun
    present with ZERO entries, retried five times reporting "left 0 file(s) --
    handle likely still closing", then called Die(). Both halves were wrong:
    with zero entries no file handle exists to close, and `git worktree add`
    was MEASURED accepting an existing empty directory (exit 0, full checkout).
    The guard written to stop orphaned worktrees became the thing that stopped
    the stage, over a condition that blocks nothing.
    """
    assert "$left -eq 0" in helper_text, (
        "Reset-DisposableWorktree must special-case the zero-entry directory; "
        "without it an empty orphan is fatal and no candidate can be gated"
    )
    # The early return must come BEFORE the backoff/Die path, or it is dead code.
    idx_empty = helper_text.index("$left -eq 0")
    idx_die = helper_text.index("could not clear $Path after")
    assert idx_empty < idx_die, (
        "the empty-directory escape must precede the fatal path"
    )


def test_empty_case_does_not_claim_a_closing_file_handle(helper_text: str) -> None:
    """The diagnosis must match the evidence.

    "handle likely still closing" is a claim about a FILE. It may only be
    printed on the branch where files actually remain -- otherwise the message
    sends the next reader hunting something that cannot exist, which is how a
    15-second false alarm becomes an hour of someone's time.
    """
    handle_line = [ln for ln in helper_text.splitlines()
                   if "handle likely still closing" in ln]
    assert handle_line, "expected the closing-handle diagnostic to still exist"
    for ln in handle_line:
        assert "$left file(s)" in ln, (
            "the closing-handle message must be attached to a nonzero file "
            f"count, got: {ln.strip()}"
        )
