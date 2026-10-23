"""The COLUMN referent check is ARMED, and it is armed behind a RATCHET.

Context (cycle-0187, #4080). `referent-verify` armed ROUTES on 2026-08-26 and
TABLES on 2026-08-27. COLUMNS stayed REPORT-ONLY for 40 days on one argument,
restated three times in its own workflow header:

    "Arming it today would turn every PR on the repo red. That is not
     enforcement, it is an outage. It is its own pass."

The argument was true and the conclusion still cost us: over those 40 days the
report-only backlog grew from 115 to 122. A report-only check does not hold a
line, it watches the line move. #4080 therefore sat waiting on a ruling
("arm columns, or not") that nothing could deliver, because both answers were
bad.

So the backlog is ENUMERATED instead -- by name AND by call-site count, in
schema/referent_column_quarantine.txt -- and everything not enumerated is
enforced. The site count is what makes it a ratchet rather than a permanent
excuse. Three states are RED:

  1. a column name that is missing and NOT listed      -> the new phantom
  2. a listed name that GAINED a call site             -> the spread
  3. a listed name that now RESOLVES                   -> the stale line

and one is deliberately not:

  4. a listed name that LOST a call site -> fine, reported by --triage-columns.
     Reddening CI for cleaning something up is how a gate teaches people to
     route around it (R7: recovery over restriction).

This is the same constructor cycle-0162 used to cure the pytest allow-list:
the enforced set is CONSTRUCTED from live state minus an explicit, reasoned
exclusion list, so the default for anything new is ENFORCED. A list a human
must remember to extend is rule 1's prose remedy wearing a YAML hat.

Every assertion below carries its own MUTANT: the real checker is fed a
deliberately broken input and must reject it. An assertion never observed red
is not evidence (R4), so the red is produced on EVERY run rather than claimed
once in a PR body -- which is precisely the failure cycle-0162 found in
0158/0159/0160.

Stdlib only, by design: this must run wherever the evaluator runs.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "referent-verify.yml")
QUARANTINE = os.path.join(ROOT, "schema", "referent_column_quarantine.txt")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import referent_verify as rv  # noqa: E402


def _write(p, text: str, bom: bool = False) -> str:
    enc = "utf-8-sig" if bom else "utf-8"
    with open(p, "w", encoding=enc, newline="\n") as fh:
        fh.write(text)
    return str(p)


GOOD = "mcp_server_registry.phantom_a sites=2  # pre-existing; first site x.py\n"


# ---------------------------------------------------------------- the parser --

def test_the_real_quarantine_parses_and_every_entry_has_a_reason():
    q = rv.load_column_quarantine(QUARANTINE)
    assert q, "the shipped quarantine is empty -- that would arm 122 names at once"
    for key, entry in q.items():
        assert "." in key, key
        assert entry["reason"], f"{key} carries no reason"
        assert entry["sites"] >= 1, f"{key} has a site budget of 0"


def test_an_absent_quarantine_is_UNKNOWN_never_an_empty_exclusion_set(tmp_path):
    """R6: unknown is not zero.

    The dangerous reading is the quiet one. If an unreadable list meant "nothing
    is excluded", a deleted or renamed file would ARM all 122 names at once and
    redden every PR on the repository -- the exact outage this construction
    exists to avoid, arriving silently.
    """
    missing = tmp_path / "nope.txt"
    with pytest.raises(rv.QuarantineError) as exc:   # MUTANT: observed red
        rv.load_column_quarantine(missing)
    assert "ABSENT" in str(exc.value)


def test_an_entry_without_a_reason_is_refused(tmp_path):
    p = _write(tmp_path / "q.txt", GOOD + "mcp_server_registry.phantom_b sites=1\n")
    with pytest.raises(rv.QuarantineError) as exc:   # MUTANT: observed red
        rv.load_column_quarantine(p)
    assert "reason is REQUIRED" in str(exc.value)


def test_a_duplicate_entry_is_refused_so_the_looser_budget_cannot_win(tmp_path):
    p = _write(tmp_path / "q.txt",
               GOOD + "mcp_server_registry.phantom_a sites=999  # looser\n")
    with pytest.raises(rv.QuarantineError) as exc:   # MUTANT: observed red
        rv.load_column_quarantine(p)
    assert "re-lists" in str(exc.value)


def test_a_bom_does_not_make_the_loader_reject_its_own_file(tmp_path):
    """The defect this cycle's own negative control found.

    Every Windows editor and PowerShell's `Set-Content -Encoding UTF8` write a
    BOM. Read as plain utf-8 it lands as \\ufeff on line 1, the first entry
    fails to match, and an ARMED required check reports UNKNOWN because of a
    byte nobody can see. The first control run hit exactly this and published
    three verdicts about code that never executed -- this repo's own documented
    failure class, inside the harness written to enforce it.
    """
    p = _write(tmp_path / "q.txt", GOOD, bom=True)
    q = rv.load_column_quarantine(p)                 # must NOT raise
    assert "mcp_server_registry.phantom_a" in q


def test_comments_and_blank_lines_are_not_entries(tmp_path):
    p = _write(tmp_path / "q.txt", "# a comment\n\n   \n" + GOOD)
    assert list(rv.load_column_quarantine(p)) == ["mcp_server_registry.phantom_a"]


# ------------------------------------------------------------- the three reds --

def _q(**kw):
    return {k: {"sites": v, "reason": "r", "lineno": i + 1}
            for i, (k, v) in enumerate(kw.items())}


def test_an_unlisted_missing_column_is_ENFORCED():
    j = rv.judge_columns({"t.unlisted": ["a.py:1"]}, {"t.unlisted": 1}, _q())
    assert list(j["unlisted"]) == ["t.unlisted"]      # MUTANT: observed red
    assert not j["spread"] and not j["stale"]


def test_a_listed_column_at_its_budget_is_excused():
    q = {"t.c": {"sites": 2, "reason": "r", "lineno": 1}}
    j = rv.judge_columns({"t.c": ["a.py:1", "b.py:2"]}, {"t.c": 2}, q)
    assert not j["unlisted"] and not j["spread"] and not j["stale"]


def test_a_listed_column_that_GAINS_a_site_is_ENFORCED():
    """The ratchet. Without this, every quarantined name is a permanent licence
    to spread that name to new code."""
    q = {"t.c": {"sites": 2, "reason": "r", "lineno": 7}}
    j = rv.judge_columns({"t.c": ["a.py:1", "b.py:2", "c.py:3"]}, {"t.c": 3}, q)
    assert j["spread"]["t.c"]["budget"] == 2         # MUTANT: observed red
    assert j["spread"]["t.c"]["observed"] == 3
    assert not j["unlisted"]


def test_a_listed_column_that_LOSES_a_site_is_not_a_failure_but_is_reported():
    """R7. Reddening CI for a cleanup is how a gate gets routed around."""
    q = {"t.c": {"sites": 5, "reason": "r", "lineno": 7}}
    j = rv.judge_columns({"t.c": ["a.py:1"]}, {"t.c": 1}, q)
    assert not j["unlisted"] and not j["spread"] and not j["stale"]
    assert j["shrunk"]["t.c"] == {"budget": 5, "observed": 1, "lineno": 7}


def test_a_listed_column_that_now_RESOLVES_is_ENFORCED_so_the_list_drains():
    """Without this the quarantine only ever grows, and a 'cure' can be
    recorded for a name that was fixed years earlier."""
    j = rv.judge_columns({}, {}, _q(**{"t.resolved_now": 1}))
    assert list(j["stale"]) == ["t.resolved_now"]     # MUTANT: observed red
    assert j["stale"]["t.resolved_now"]["lineno"] == 1


def test_the_site_count_is_taken_UNCAPPED_not_from_the_display_list():
    """report["missing"] truncates sites to 5 for display. Judging the ratchet
    on that truncated list would silently cap every budget at 5 and let any
    name with 6+ sites spread without limit."""
    q = {"t.c": {"sites": 5, "reason": "r", "lineno": 1}}
    j = rv.judge_columns({"t.c": ["a:1", "b:2", "c:3", "d:4", "e:5"]},
                         {"t.c": 9}, q)
    assert j["spread"]["t.c"]["observed"] == 9        # MUTANT: observed red


# ---------------------------------------------------------------- the arming --

def _workflow_text() -> str:
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def _enforce_args(text: str) -> list[str]:
    """Every --enforce-checks value in the workflow, comments stripped.

    FU-305's class: a commented-out line reads as live to a raw-text scan.
    """
    out = []
    for line in text.splitlines():
        bare = line.split("#", 1)[0]
        if "--enforce-checks" in bare:
            tail = bare.split("--enforce-checks", 1)[1].strip().strip("\\").strip()
            out.append(tail)
    return out


def test_the_workflow_arms_columns():
    """The cure asserted, not described. If someone disarms columns to make a
    red PR green, this fails."""
    args = _enforce_args(_workflow_text())
    assert args, "no live --enforce-checks invocation found in the workflow"
    for a in args:
        checks = {c.strip() for c in a.split(",") if c.strip()}
        assert "columns" in checks, f"columns is not armed: {a!r}"
        assert "tables" in checks, f"tables lost its arming: {a!r}"
        assert "routes" in checks, f"routes lost its arming: {a!r}"


def test_the_enforce_args_scanner_ignores_a_commented_out_invocation():
    assert _enforce_args("  #   --enforce-checks routes\n") == []   # MUTANT
    assert _enforce_args("  --enforce-checks routes,tables\n") == ["routes,tables"]


def test_the_workflow_still_has_no_continue_on_error_or_skip_as_success():
    text = _workflow_text()
    verify = text.split("Verify referents", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error" not in verify
    assert "|| true" not in verify


def test_the_quarantine_file_is_tracked_and_not_empty():
    """A gate whose exclusion list is untracked arms differently in CI than on
    anyone's box -- the B2 shape, and FU-568's (a one-line fix that could not be
    delivered because the file was not in git)."""
    assert os.path.exists(QUARANTINE), QUARANTINE
    with open(QUARANTINE, encoding="utf-8-sig") as fh:
        body = [l for l in fh if l.strip() and not l.lstrip().startswith("#")]
    assert len(body) >= 1
