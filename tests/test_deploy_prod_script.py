"""Safety contract for ops/host/deploy_prod.ps1 -- the chairman's one-click.

This is the ONE path that actually writes prod, and it is fired by hand, rarely,
under time pressure. Its failure modes are therefore the expensive kind: nobody
is watching a log they only read once a week.

The invariants below are the ones whose absence has already cost a run:

  * the disposable worktree must be healed BEFORE creation and its teardown
    VERIFIED after -- on 2026-07-27 `git worktree remove --force` pruned the
    metadata and left 3,466 files on disk, `git worktree list` showed nothing,
    and the NEXT run died on "already exists". This wrapper still carried the
    un-hardened form (`Remove-Item ... -ErrorAction SilentlyContinue` with no
    check after it) for a day after the staging script was fixed;
  * the two call sites must differ in strictness. A stale path BEFORE the add is
    fatal -- `worktree add` cannot pin the sha, so deploying anyway ships an
    unknown tree. Leftover files AFTER the deploy must NOT be fatal: the deploy
    and its acceptance gate are the verdict, and failing the script over
    teardown would report a successful ship as a failure;
  * the build args must survive every future edit to this file. `/version`
    served git_sha="unknown" from v64 onward precisely because a deploy went out
    without them (#2063), and the acceptance gate asserts on that field;
  * a rollback anchor must be resolved before any deploy.

These are text assertions on purpose: PowerShell is not importable from pytest,
and the alternative -- running the script -- would deploy to prod. Same pattern
as tests/test_verify_candidate_script.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "ops" / "host" / "deploy_prod.ps1"
# FU-157: the helper is no longer inline. It was copied into this script AND
# into verify_candidate.ps1, the copies diverged, and the branch that mattered
# (an EMPTY leftover is harmless) reached only the observer -- so the fire path
# halted on a condition measured to block nothing. One definition now; these
# assertions FOLLOW the dot-source rather than assume co-location. They lose no
# power: what must still be proven is that deploy_prod.ps1 REACHES a helper with
# bounded retry and a post-removal proof, and every one of them can still go red.
LIFECYCLE = REPO_ROOT / "ops" / "host" / "worktree_lifecycle.ps1"

HELPER = "Reset-DisposableWorktree"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT.is_file(), f"missing deploy wrapper: {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def helper_text(script_text: str) -> str:
    """Resolve the helper the way PowerShell will at runtime.

    The dot-source must name $PSScriptRoot, never $WorktreePath: the helper
    belongs to the RUNBOOK, not to the tree being shipped, which generally
    predates it. Resolving it inside the checked-out worktree would make the
    guard vanish exactly when deploying an older sha.
    """
    assert re.search(
        r"\.\s+\(Join-Path\s+\$PSScriptRoot\s+[\"']worktree_lifecycle\.ps1[\"']\)",
        script_text,
    ), (
        f"{SCRIPT.name} must dot-source ops/host/worktree_lifecycle.ps1 from "
        "$PSScriptRoot -- without that link it has no teardown at all."
    )
    assert LIFECYCLE.is_file(), f"missing worktree lifecycle helper: {LIFECYCLE}"
    return LIFECYCLE.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Drop comments and the comment-based help block.

    The script documents the hazards it guards against by quoting them, so a
    naive substring search would happily match its own rationale.
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
    return "\n".join(out)


# --------------------------------------------------------------- teardown


def _helper_body(code: str) -> str:
    """Slice EXACTLY the helper function, brace-matched.

    The bound matters. An earlier cut of this file sliced from the function name
    to end-of-file, so `assert "Start-Sleep" in body` was satisfied by the
    ACCEPTANCE-GATE POLL a hundred lines below -- the test stayed green with the
    retry backoff deleted. A negative control caught it; nothing else would have.
    """
    start = code.index(f"function {HELPER}")
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start:i + 1]
    raise AssertionError(f"unbalanced braces in {HELPER}")


def test_helper_is_defined(script_text: str, helper_text: str) -> None:
    """The teardown must EXIST and be REACHABLE from the deploy path.

    Two assertions, and the second is the FU-157 one: the script must not carry
    its own copy. A second definition cannot be kept in agreement by intention,
    and it drifts in a predictable direction -- the fix lands in whichever copy
    you were looking at when you learned the lesson, which is never the actor.
    """
    assert f"function {HELPER}" in _code(helper_text), (
        f"ops/host/worktree_lifecycle.ps1 must define {HELPER} -- the verified, "
        "bounded-retry teardown proven in verify_candidate.ps1 (#2066/#2067)."
    )
    assert f"function {HELPER}" not in _code(script_text), (
        f"{SCRIPT.name} must NOT redefine {HELPER}; dot-source the single source "
        "instead. Duplicating it is what caused FU-157."
    )


def test_teardown_is_verified_after_removal_not_merely_attempted(helper_text: str) -> None:
    """The post-removal proof must come AFTER the removal, inside the helper.

    Written this way deliberately: the first cut of the sibling test asserted
    only that "Test-Path $Path" appeared somewhere in the file, which also
    matched the PRE-add heal -- so it stayed green with the post-teardown proof
    deleted. An assertion that cannot go red is not evidence.
    """
    body = _helper_body(_code(helper_text))

    removal = body.index("Remove-Item -LiteralPath $Path")
    proof = body.index("if (-not (Test-Path $Path))")
    assert proof > removal, (
        "the Test-Path proof must follow the Remove-Item it is proving; "
        "a check that runs before the delete proves nothing."
    )


def test_retry_is_bounded_not_infinite_and_not_single_shot(helper_text: str) -> None:
    body = _helper_body(_code(helper_text))
    assert "$Attempts" in body, "retry must be bounded by an attempt count"
    assert "Start-Sleep" in body, (
        "a retry with no backoff re-checks a still-closing handle instantly "
        "and learns nothing"
    )


def test_no_unverified_worktree_removal_survives(script_text: str) -> None:
    """Every removal of the disposable path must go through the helper."""
    code = _code(script_text)
    for line in code.splitlines():
        if "Remove-Item" not in line or "$WorktreePath" not in line:
            continue
        pytest.fail(
            "raw Remove-Item on $WorktreePath outside the helper -- this is the "
            f"silent-orphan form. Route it through {HELPER}. Offending line: "
            f"{line.strip()}"
        )


def test_call_sites_differ_in_strictness(script_text: str) -> None:
    code = _code(script_text)
    calls = [ln.strip() for ln in code.splitlines() if f"({HELPER} " in ln]
    assert len(calls) == 2, (
        f"expected exactly two {HELPER} call sites (pre-add heal, post-deploy "
        f"teardown); found {len(calls)}: {calls}"
    )
    strict = [c for c in calls if "-MustSucceed $true" in c]
    lenient = [c for c in calls if "-MustSucceed $false" in c]
    assert len(strict) == 1, (
        "the PRE-add heal must be fatal: a path that will not clear cannot be "
        "pinned to the sha, and deploying anyway ships an unknown tree."
    )
    assert len(lenient) == 1, (
        "the POST-deploy teardown must NOT be fatal: the deploy and its "
        "acceptance gate are the verdict. Loud, not fatal, not silent."
    )
    assert code.index(strict[0]) < code.index(lenient[0]), (
        "the fatal heal runs before the worktree is created; the lenient "
        "teardown runs after the deploy"
    )


# ------------------------------------------------- regressions on the deploy


def test_build_args_still_passed(script_text: str) -> None:
    """#2063. /version has served git_sha="unknown" since v64 without these."""
    code = _code(script_text)
    assert "GIT_SHA=$Sha" in code, (
        "the deploy must stamp GIT_SHA or /version reports 'unknown' and the "
        "acceptance gate cannot prove the running image came from the gated tree"
    )
    assert "BUILD_TIME=" in code


def test_rollback_anchor_is_required_before_deploy(script_text: str) -> None:
    code = _code(script_text)
    assert "refusing to deploy without one" in code or "$RollbackImage" in code
    assert "flyctl deploy --app $App --image $RollbackImage" in code, (
        "a deploy with no printed rollback command is a one-way door"
    )


def test_full_sha_is_required(script_text: str) -> None:
    assert "'^[0-9a-f]{40}$'" in _code(script_text), (
        "short shas make the deployed identity ambiguous"
    )
