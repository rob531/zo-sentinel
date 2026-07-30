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
