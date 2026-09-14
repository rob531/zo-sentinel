"""E10: an entry whose keys are BARE LINES parses, counts, and is inert.

2026-09-13, improvement-loop cycle-0102. `fu_ledger.KEY_RE` requires keys to be
`- key: value` BULLETS. An entry that writes them as bare lines (`status: open`)
still matches HEAD_RE, so it parses, the entry count goes up, and a heading-form
check reports the file clean -- but KEY_RE never matches, `fu.keys` is empty,
there is no `verify_cmd`, and `fu_verify.py` has nothing to execute. Two such
entries were found live that day, each holding a real predicate that had never
once run.

The discriminating pair below is the whole test: two entries identical except
for a leading `- ` on each key. If E10 fires on both or neither, it is measuring
something other than key form.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger_lint  # noqa: E402


HEAD = "### FU-700 | an entry used only by this test"
KEYS = [
    ("status", "open"),
    ("priority", "P3"),
    ("class", "defect"),
    ("date", "2026-09-13"),
    ("detail", "written twice, once per key form"),
    ("resolution", ""),
    ("verify", "`python -c \"raise SystemExit(0)\"`"),
    ("verify_seen_red", "2026-09-13"),
]


def _ledger(bullets):
    out = [HEAD, ""]
    for k, v in KEYS:
        out.append(("- %s: %s" if bullets else "%s: %s") % (k, v))
    out.append("")
    return out


def _codes(lines):
    _entries, errors = ledger_lint.analyse(lines)
    return [e["code"] for e in errors]


def test_bullet_keys_do_not_raise_e10():
    """GREEN pole. The sanctioned form must not trip the new check."""
    assert "E10" not in _codes(_ledger(True))


def test_bare_line_keys_raise_e10():
    """RED pole. This is the form that shipped two inert entries."""
    assert "E10" in _codes(_ledger(False))


def test_the_pair_differs_only_by_the_bullet():
    """Guards against E10 firing for some unrelated reason."""
    good, bad = _ledger(True), _ledger(False)
    assert len(good) == len(bad)
    diffs = [(g, b) for g, b in zip(good, bad) if g != b]
    assert diffs, "the fixtures are identical -- the test proves nothing"
    for g, b in diffs:
        assert g == "- " + b


def test_keyless_entry_still_parses_and_counts():
    """The reason this was invisible: nothing downstream looked wrong."""
    entries, _ = ledger_lint.analyse(_ledger(False))
    assert len(entries) == 1
    fu = entries[0]
    assert str(fu.num) == "700"
    assert not fu.keys
    assert not getattr(fu, "verify_cmd", None)


def test_repair_leaves_a_keyless_entry_alone():
    """A keyless entry needs FORM repair; inserting keys would mask it."""
    lines = _ledger(False)
    entries, _ = ledger_lint.analyse(lines)
    before = list(lines)
    ledger_lint.repair(lines, entries)
    assert lines == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
