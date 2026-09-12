"""append_log must not split a wrapped log bullet.

test_append_after_wrapped_bullet_is_the_negative_control is the control: it
FAILS against the pre-fix scan (which stepped only over `  - ` head lines and
therefore inserted the new bullet between a bullet and its own wrapped prose,
silently re-parenting that prose to the new entry). Seen RED on
2026-07-30 before the fix; if it stops failing without the fix, the guard is
no longer being tested.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fu_ledger  # noqa: E402


def _ledger(log_block):
    return [
        "# FOLLOWUPS",
        "",
        "### FU-900 | A sample entry whose log bullets wrap",
        "- date: 2026-07-30 - source: test - status: resolved - priority: P2",
        "- class: defect",
        "- detail: a detail.",
        "- verify: NONE - legacy entry, predicate not yet written",
    ] + log_block + [
        "- lesson: **A LESSON THAT MUST STAY OUTSIDE THE LOG BLOCK.**",
        "",
        "### FU-901 | The next entry",
        "- date: 2026-07-30 - source: test - status: open - priority: P3",
        "- class: defect",
        "- detail: another detail.",
    ]


WRAPPED = [
    "- log:",
    "  - 2026-07-28T11:05Z sibling: the first line of an existing entry.",
    "    a wrapped continuation line that belongs to the 07-28 bullet.",
    "    a second wrapped continuation line, also the 07-28 bullet's.",
]


def _fu(lines, num="900"):
    return [f for f in fu_ledger.parse(lines) if f.num == num][0]


def test_append_after_wrapped_bullet_is_the_negative_control():
    """The new bullet lands AFTER the whole wrapped block, not inside it."""
    lines = _ledger(WRAPPED)
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-07-30T07:20Z me: new entry.")

    assert lines[pos] == "  - 2026-07-30T07:20Z me: new entry."
    # every wrapped line still sits above the new bullet, i.e. still attached
    # to the bullet that owns it.
    for wrapped in WRAPPED[2:]:
        assert lines.index(wrapped) < pos, "wrapped prose was re-parented"
    # and the new bullet is the last line of the log block.
    assert lines[pos + 1] == "- lesson: **A LESSON THAT MUST STAY OUTSIDE THE LOG BLOCK.**"


def test_append_to_unwrapped_log_block_still_appends_last():
    lines = _ledger([
        "- log:",
        "  - 2026-07-28T11:05Z sibling: a single-line entry.",
        "  - 2026-07-29T11:05Z sibling: another single-line entry.",
    ])
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-07-30T07:20Z me: new entry.")
    assert lines[pos - 1] == "  - 2026-07-29T11:05Z sibling: another single-line entry."
    assert lines[pos + 1].startswith("- lesson:")


def test_append_creates_the_log_key_when_absent():
    lines = _ledger([])
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-07-30T07:20Z me: new entry.")
    assert lines[pos - 1] == "- log:"
    assert lines[pos] == "  - 2026-07-30T07:20Z me: new entry."


def test_appended_bullet_reparses_under_the_right_fu():
    lines = _ledger(WRAPPED)
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-07-30T07:20Z me: new entry.")
    fu = _fu(lines)
    assert fu.start < pos < fu.end
    assert _fu(lines, "901").start > pos


def test_append_does_not_cross_into_the_next_fu():
    """A log block that runs to the end of its entry must still stop there."""
    lines = _ledger(WRAPPED)
    del lines[-6]  # drop the `- lesson:` line so the log block ends the entry
    fu = _fu(lines)
    pos = fu_ledger.append_log(lines, fu, "2026-07-30T07:20Z me: new entry.")
    assert pos < _fu(lines, "901").start
    assert lines[pos] == "  - 2026-07-30T07:20Z me: new entry."


# --------------------------------------------------------------------------
# keepends: the 2026-08-02 glue bug.
#
# A caller that passes `splitlines(keepends=True)` used to get an inserted
# string with NO terminator, so `"".join(lines)` ran it straight into the line
# below. That is how an append to FU-054 swallowed its `- resolution:` key: the
# ledger still PARSED afterwards, so no check went red. These two tests are the
# negative control -- both FAIL against the pre-fix fu_ledger.
# --------------------------------------------------------------------------

def _kept(lines, eol="\n"):
    """Same fixture, but terminated -- what `splitlines(keepends=True)` yields."""
    return [ln + eol for ln in lines]


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_append_with_keepends_does_not_glue_the_following_key(eol):
    """NEGATIVE CONTROL: the key below the log block must survive the append."""
    lines = _kept(_ledger(WRAPPED), eol)
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-08-02T11:05Z me: new entry.")

    assert lines[pos] == "  - 2026-08-02T11:05Z me: new entry." + eol
    # the join is the thing that actually broke, so assert on the JOINED text
    joined = "".join(lines)
    assert "new entry." + eol + "- lesson:" in joined, "the following key was glued"
    assert joined.count("- lesson:") == 1
    # and the whole document still round-trips to the same line count
    assert len(joined.splitlines()) == len(lines)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_insert_key_with_keepends_does_not_glue(eol):
    lines = _kept(_ledger(WRAPPED), eol)
    fu_ledger.insert_key(lines, _fu(lines), "verify_seen_red", "NEVER", before="log")
    joined = "".join(lines)
    assert "- verify_seen_red: NEVER" + eol in joined
    assert len(joined.splitlines()) == len(lines)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_append_creates_log_key_with_keepends(eol):
    lines = _kept(_ledger([]), eol)
    pos = fu_ledger.append_log(lines, _fu(lines), "2026-08-02T11:05Z me: new entry.")
    assert lines[pos - 1] == "- log:" + eol
    assert lines[pos] == "  - 2026-08-02T11:05Z me: new entry." + eol
    assert len("".join(lines).splitlines()) == len(lines)


def test_line_terminator_detects_the_convention():
    assert fu_ledger.line_terminator(["a", "b"]) == ""
    assert fu_ledger.line_terminator(["a\n", "b\n"]) == "\n"
    assert fu_ledger.line_terminator(["a\r\n", "b\r\n"]) == "\r\n"
    assert fu_ledger.line_terminator([]) == ""
