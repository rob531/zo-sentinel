"""fu_ledger WRITE side: a PATH must write, as a path already READS.

improvement-loop cycle-0088.

MEASURED ASYMMETRY. Cycle-0066 made every reader accept a path, so
`parse(LEDGER)` returns 418 entries on this tower today. The writers never
followed. A caller who learns the reader takes a path infers the writer does
too, and the `sanctioned-writer-api-shape` family's most recent bites are
exactly that inference, made by three different lanes:

  2026-09-03T11:58Z x2  plan-200k-count-tracker  append_log(LEDGER_PATH, 237, text)
  2026-09-09T11:39Z     vast-jobs-daily-audit    append_log(LEDGER, fu, text)
                        -- "Cost one full spawn+poll cycle."

Cycle-0065 made that call raise a paragraph naming the CLI door. A paragraph is
reachable only AFTER the bite; the round trip is already spent. HARNESS_DOCTRINE
R7 prefers RECOVERY over RESTRICTION, and cycle-0066 already made that move on
the read half of the same module. This is the write half.

Every test carries BOTH poles:

  * POSITIVE CONTROL -- the list-first shape every correct caller uses must be
    byte-for-byte unchanged, including the pure-function property (the file on
    disk is NOT touched when you pass lines). A cure that quietly starts writing
    files for pure callers would be worse than the defect.
  * NEGATIVE POLE -- the path-first shape, which raised TypeError before this
    commit.
  * ANTI-GUESS CONTROLS -- a path that does not exist, and an unknown FU, must
    still refuse. A silent no-op is the defect this whole family is made of, and
    `test_failed_verify_restores` proves the failure path RESTORES rather than
    leaving a half-written ledger.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "fu" / "fu_ledger.py"

LEDGER_CRLF = (
    "# Follow-ups\r\n"
    "\r\n"
    "### FU-035 | leading-zero entry\r\n"
    "- date: 2026-01-01 - status: OPEN\r\n"
    "- log:\r\n"
    "  - 2026-01-01 opened\r\n"
    "\r\n"
    "### FU-395 | the entry under test\r\n"
    "- date: 2026-09-09 - status: OPEN\r\n"
    "- log:\r\n"
    "  - 2026-09-09 pre-existing bullet\r\n"
    "\r\n"
)

MARK = "write-path-shape probe bullet"


@pytest.fixture(scope="module")
def fu_ledger():
    spec = importlib.util.spec_from_file_location("fu_ledger_wp", MODULE)
    assert spec and spec.loader, "fu_ledger.py not importable at %s" % MODULE
    mod = importlib.util.module_from_spec(spec)
    # sys.modules BEFORE exec_module: the dataclass body resolves its own module
    # by name and dies without this, identically on every copy.
    sys.modules["fu_ledger_wp"] = mod
    spec.loader.exec_module(mod)
    return mod


def ledger(tmp_path, body=LEDGER_CRLF, name="FOLLOWUPS.md"):
    p = tmp_path / name
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return p


def read(p):
    with io.open(p, encoding="utf-8", newline="") as fh:
        return fh.read()


# ------------------------------------------------------------ positive controls

def test_list_shape_unchanged_and_still_pure(fu_ledger, tmp_path):
    """POSITIVE CONTROL. lines-first must behave exactly as before: mutate the
    list, return an index, and NOT touch any file."""
    p = ledger(tmp_path)
    before = read(p)
    lines = before.splitlines(keepends=True)
    n = len(lines)
    pos = fu_ledger.append_log(lines, 395, "2026-09-09 " + MARK)
    assert isinstance(pos, int)
    assert len(lines) == n + 1, "the list was not mutated in place"
    assert read(p) == before, "the pure shape wrote to disk -- that is a regression"


def test_insert_key_list_shape_unchanged(fu_ledger, tmp_path):
    p = ledger(tmp_path)
    before = read(p)
    lines = before.splitlines(keepends=True)
    fu_ledger.insert_key(lines, "395", "class", "defect")
    assert any(l.startswith("- class: defect") for l in lines)
    assert read(p) == before


# --------------------------------------------------------------- negative poles

def test_append_log_accepts_a_path_and_writes(fu_ledger, tmp_path):
    """NEGATIVE POLE: raised TypeError on every copy before cycle-0088."""
    p = ledger(tmp_path)
    pos = fu_ledger.append_log(p, 395, "2026-09-09 " + MARK)
    assert isinstance(pos, int) and pos >= 0
    after = read(p)
    assert MARK in after, "the bullet is not in the file"
    lines = after.splitlines(keepends=True)
    assert after.count("\r\n") == len(lines), "CRLF class did not survive the write"
    assert len(lines) == len(LEDGER_CRLF.splitlines(keepends=True)) + 1


def test_append_log_accepts_a_str_path(fu_ledger, tmp_path):
    p = ledger(tmp_path)
    fu_ledger.append_log(str(p), "FU-395", "2026-09-09 " + MARK)
    assert MARK in read(p)


def test_insert_key_accepts_a_path(fu_ledger, tmp_path):
    """The sibling writer, cured in the SAME commit."""
    p = ledger(tmp_path)
    fu_ledger.insert_key(p, 395, "class", "defect")
    assert "- class: defect" in read(p)


def test_lf_ledger_stays_lf(fu_ledger, tmp_path):
    """This file's terminators have flipped twice; the class must not move."""
    p = ledger(tmp_path, LEDGER_CRLF.replace("\r\n", "\n"))
    fu_ledger.append_log(p, 395, "2026-09-09 " + MARK)
    after = read(p)
    assert MARK in after
    assert after.count("\r") == 0, "an LF ledger gained CRs"


def test_path_mode_backs_the_ledger_up(fu_ledger, tmp_path):
    p = ledger(tmp_path)
    fu_ledger.append_log(p, 395, "2026-09-09 " + MARK)
    backups = list((tmp_path / "_followup_backups").rglob("FOLLOWUPS.md.pre-write-*"))
    assert backups, "no backup was taken before a destructive write"
    assert read(backups[0]) == LEDGER_CRLF, "the backup is not the pre-write state"


def test_if_absent_makes_a_retry_converge(fu_ledger, tmp_path):
    """IDEMPOTENCE. A re-run of a lane must not double the bullet."""
    p = ledger(tmp_path)
    first = fu_ledger.append_log(p, 395, "2026-09-09 " + MARK, if_absent=MARK)
    assert first >= 0
    second = fu_ledger.append_log(p, 395, "2026-09-09 " + MARK, if_absent=MARK)
    assert second == -1, "the second call was not reported as a no-op"
    assert read(p).count(MARK) == 1, "a retry duplicated the bullet"


# ------------------------------------------------------------ anti-guess controls

def test_nonexistent_path_still_refuses(fu_ledger, tmp_path):
    """A missing path is UNKNOWN, not an empty ledger, and must not be created."""
    missing = tmp_path / "NO_SUCH_LEDGER.md"
    with pytest.raises(TypeError):
        fu_ledger.append_log(missing, 395, "2026-09-09 " + MARK)
    assert not missing.exists(), "a refused write created the file"


def test_unknown_fu_in_path_mode_leaves_the_file_alone(fu_ledger, tmp_path):
    """The anti-guess control. An unknown FU must not silently no-op, and must
    not leave a half-written ledger behind."""
    p = ledger(tmp_path)
    before = read(p)
    with pytest.raises(ValueError):
        fu_ledger.append_log(p, 999, "2026-09-09 " + MARK)
    assert read(p) == before, "the ledger changed on a refused write"


def test_failed_verify_restores_the_ledger(fu_ledger, monkeypatch, tmp_path):
    """The verify must be able to FAIL and must restore when it does.

    Without this pole `_write_through`'s recovery branch would never once be
    observed running, which is R4: an assertion never seen red is not evidence.
    """
    p = ledger(tmp_path)
    before = read(p)

    def corrupt(lines, entries):
        lines[:] = ["### FU-035 | only one entry left\n"]
        return 0

    with pytest.raises(fu_ledger.LedgerWriteFailed) as exc:
        fu_ledger._write_through("append_log", p, corrupt)
    assert "RESTORED" in str(exc.value)
    assert read(p) == before, "a failed write was not rolled back"
