"""fu_ledger read-side call shape: a path argument must not answer with a lie.

FU-385 / improvement-loop cycle-0066.

`parse` and `line_terminator` take a LIST OF LINES. Handed a path STRING they
iterate the string's CHARACTERS: parse matched no heading and returned `[]`,
line_terminator found no terminator and returned `""`. Neither raised. On a
379-entry ledger the first reads as "the ledger is empty" and the second makes
the caller write UNTERMINATED lines into a CRLF file. `vast-jobs-daily-audit`
was bitten by the parse form on 2026-09-03; the family (`sanctioned-writer-api-
shape`) has 13 recorded bites across 8 lanes in seven days.

Cycle-0065 cured the WRITE side (append_log, insert_key) with a TypeError that
names the real signature. This is the read side, and the cure is RECOVERY
rather than a louder refusal (HARNESS_DOCTRINE R7): a path is an unambiguous
request for that file's lines, so read it.

Each test below has BOTH poles. The negative pole is the shape that used to
return a plausible lie; the positive pole is the list-first call every correct
caller already makes, which must be byte-identical to before -- a cure that
breaks the shape people get RIGHT is not a cure.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "fu" / "fu_ledger.py"

LEDGER_CRLF = (
    "# Follow-ups\r\n"
    "\r\n"
    "### FU-001 | first entry\r\n"
    "- date: 2026-01-01 - status: OPEN\r\n"
    "- log:\r\n"
    "  - 2026-01-01 opened\r\n"
    "\r\n"
    "### FU-002 | second entry\r\n"
    "- date: 2026-01-02 - status: CLOSED\r\n"
)


@pytest.fixture(scope="module")
def fu_ledger():
    spec = importlib.util.spec_from_file_location("fu_ledger_uut", str(MODULE))
    mod = importlib.util.module_from_spec(spec)
    # Seed sys.modules BEFORE exec_module: without it dataclasses cannot resolve
    # the module by name and the import dies with a confusing KeyError.
    sys.modules["fu_ledger_uut"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ledger_path(tmp_path):
    p = tmp_path / "FOLLOWUPS.md"
    p.write_bytes(LEDGER_CRLF.encode("utf-8"))
    return p


def _lines(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().splitlines(keepends=True)


def test_parse_of_a_path_matches_parse_of_its_lines(fu_ledger, ledger_path):
    expected = fu_ledger.parse(_lines(ledger_path))
    assert [f.id for f in expected] == ["FU-001", "FU-002"]      # positive pole
    got = fu_ledger.parse(str(ledger_path))                      # negative pole
    assert [f.id for f in got] == [f.id for f in expected], (
        "parse(<path>) returned %d entries against %d for the same file's "
        "lines" % (len(got), len(expected)))


def test_line_terminator_of_a_path_sees_crlf(fu_ledger, ledger_path):
    assert fu_ledger.line_terminator(_lines(ledger_path)) == "\r\n"
    assert fu_ledger.line_terminator(str(ledger_path)) == "\r\n", (
        "a path argument returned '' -- the caller then writes UNTERMINATED "
        "lines into a CRLF ledger")


def test_a_path_that_does_not_resolve_is_refused_with_the_real_shape(fu_ledger,
                                                                     tmp_path):
    """Recovery is for paths that EXIST. Anything else must say what to do."""
    with pytest.raises(TypeError) as excinfo:
        fu_ledger.parse(str(tmp_path / "no-such-ledger.md"))
    msg = str(excinfo.value)
    # Assert on substrings the hint's own line-wrapping cannot split. "PURE
    # FUNCTION" looks like the obvious marker and is wrapped across a newline
    # in the source -- an assertion on it fails for a reason that has nothing
    # to do with the property under test.
    assert "takes the ledger's LINES, not a filename" in msg
    assert "splitlines(keepends=True)" in msg
    assert "fu_append_log.py" in msg


def test_a_non_path_argument_is_still_refused(fu_ledger):
    with pytest.raises(TypeError):
        fu_ledger.parse(364)


def test_the_list_first_call_is_untouched_by_the_recovery(fu_ledger,
                                                          ledger_path):
    """The cure must be invisible to correct callers, including empty input."""
    assert fu_ledger.parse([]) == []
    assert fu_ledger.line_terminator([]) == ""
    lines = _lines(ledger_path)
    before = list(lines)
    fu_ledger.parse(lines)
    assert lines == before, "parse mutated the caller's list"
