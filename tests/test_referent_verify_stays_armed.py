"""The arming must be as hard to remove as it was to earn.

WHY THIS FILE EXISTS
    Everything else in the referent story guards against a bad REFERENT. This
    guards against a bad EDIT to the guard.

    `--enforce-checks routes,tables` is one string in one YAML file. Deleting
    `,tables` disarms the tables check completely, silently, and leaves the job
    GREEN -- so the disarming looks exactly like success. Nothing in CI would
    have noticed, and the phantom-table backlog that took #4032, #4067, #4070,
    #4089 and #4080 to clear would begin refilling with no red anywhere.

    That matters more than usual here because of how somebody would arrive at
    that edit: the tables check going red on a legitimately awkward PR. The
    fastest way to make it green is to remove the word. This test makes the
    fastest way instead be "fix the referent", by putting a REQUIRED check
    (pytest) in front of the disarming.

WHAT THIS BUYS THE READER
    It collapses "is tables enforcement still working?" into one question:
    is the latest referent-verify run on main green? Without this test the
    answer would need two -- is it green, AND is it still armed -- because a
    disarmed check is green for the wrong reason.

    That is the whole point of the exercise. A control you have to verify with
    two questions is a control someone checks with one.

DELIBERATELY NOT ASSERTED HERE
    That `columns` is absent. Columns is report-only at 114 missing and arming
    it is its own pass; asserting its absence would have to be deleted by the
    very change that arms it, which is backwards.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "referent-verify.yml"


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.exists(), "referent-verify.yml is gone -- the gate is gone"


def test_tables_is_still_armed():
    """The one-line disarm. #4080."""
    m = re.search(r"--enforce-checks\s+([a-z,]+)", _uncommented())
    assert m, "no --enforce-checks flag in referent-verify.yml -- NOTHING is armed"
    armed = {c for c in m.group(1).split(",") if c}
    assert "tables" in armed, (
        "the TABLES check has been DISARMED. It was armed 2026-08-27 (#4080) "
        f"after the phantom-table count reached 0; armed now: {sorted(armed)}. "
        "If a legitimate PR is blocked by it, fix the referent or declare the "
        "table -- do not remove the word 'tables' from this flag.")
    assert "routes" in armed, (
        "the ROUTES check has been DISARMED. Armed 2026-08-26 (#4067). "
        f"armed now: {sorted(armed)}")


def test_there_is_no_paths_filter_on_the_trigger():
    """A required context that is skipped never reports, and the PR hangs.

    #4089. The path scoping lives in the JOB, in the 'Determine referent scope'
    step, precisely so this workflow always runs and therefore always reports.
    """
    trigger = _uncommented().split("permissions:", 1)[0]
    assert not re.search(r"^\s+paths:", trigger, re.M), (
        "referent-verify has grown a `paths:` filter on its trigger. It is a "
        "REQUIRED status check: a skipped workflow reports NO context, branch "
        "protection waits on it forever, and every docs-only PR hangs. Scope it "
        "in the job, not the trigger.")


def test_the_job_id_still_matches_the_required_context_name():
    """Renaming the job silently un-requires the gate.

    Branch protection requires the context `referent-verify`, which IS the job
    id. Rename the job and protection keeps waiting on a context nothing
    reports -- every PR hangs, and the gate is off.
    """
    assert re.search(r"^\s{2}referent-verify:\s*$", _uncommented(), re.M), (
        "the job id `referent-verify` is gone. That is the required status "
        "context name in branch protection. If it must be renamed, change the "
        "required set in the SAME change.")


def _uncommented():
    """The YAML with its comment lines removed.

    The header of referent-verify.yml *documents* the rules below in prose --
    "There is no `continue-on-error` and no `|| true` on any path that decides
    the verdict" -- so a naive substring search matches the promise instead of
    checking whether it is kept. Strip the comments and test the YAML.
    """
    return "\n".join(l for l in _text().splitlines()
                      if not l.lstrip().startswith("#"))


def test_no_continue_on_error_or_swallowed_exit():
    """`|| true` on the verdict path is the other silent disarm."""
    body = _uncommented()
    assert "continue-on-error" not in body, \
        "continue-on-error makes a red check green. That is a disarm."
    verify = body.split("Verify referents", 1)[-1].split("- name:", 1)[0]
    assert "|| true" not in verify, \
        "`|| true` on the verify step discards the verdict"
    assert 'exit "$rc"' in verify, \
        "the verify step must propagate the checker's exit code"
