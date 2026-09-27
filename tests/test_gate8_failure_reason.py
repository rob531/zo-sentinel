"""c143: a Gate 8 failure must record WHY, not just WHICH COHORT.

Measured on the live tower 2026-09-26 (gate_quality_state.json, 44 of 44
file_retries entries): every persisted `last_error` reads `failed in
cohort_N_nM`. Twelve distinct values, all of them cohort labels, not one
diagnosis. `_files_this_run_bad` is declared `{filename: first_error_str}`
and is assigned in exactly ONE place -- `_cohort_bump`'s setdefault -- to
the literal `f'failed in {cohort_label}'`.

That string is what `gqs.record_failure()` persists, what the retry ledger
carries to quarantine at 3 attempts, and what the quality map injects into
every directive-generation cycle under the instruction:

    "If proposing a rebuild, you MUST reference the listed last_error
     and relevant spec section explicitly."

An instruction that cannot be followed. The reason exists -- `Gate.check()`
already has check_name, error_class, expected and actual, prints them and
writes them to the GateErrorDB -- it is simply discarded on the path that
reaches the ledger. Doctrine R6: an unknown was being published as a
diagnosis.

NEGATIVE CONTROL: test_reason_is_not_a_cohort_label FAILS against the
pre-fix tree (the dict is never populated by check(), so .get() is None)
and PASSES after. Revert the `check()` override in Gate8NewModule and this
goes red again -- observed red 2026-09-26 before the fix was written.
"""

import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO, REPO / "tests" / "gates"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

COHORT_LABEL_RE = re.compile(r"^failed in cohort_")


class _StubErrorDB:
    """Minimal GateErrorDB: record_check/record_error are the only surface
    Gate.check() touches, and neither needs to persist for this test."""

    def __init__(self):
        self.checks = []
        self.errors = []

    def record_check(self, run_id, gate, check_name, status, duration_ms=0, details=""):
        self.checks.append((check_name, status))
        return len(self.checks)

    def record_error(self, check_id, error_class, file=None, line_no=None,
                     expected="", actual="", remediation=""):
        self.errors.append((error_class, expected, actual))
        return (len(self.errors), True)


def _gate():
    import gate_8_new_module as g8
    gate = g8.Gate8NewModule(_StubErrorDB(), "run-c143")
    # run() sets these up; we are testing one file's accounting, not a run.
    gate._cohort_totals = {}
    gate._files_this_run_ok = set()
    gate._files_this_run_bad = {}
    return gate


KEY = "services/staged/example_service/router.py"


def test_reason_is_not_a_cohort_label():
    """NEGATIVE CONTROL. Red before the fix, green after."""
    gate = _gate()
    gate._current_key = KEY

    gate.check(
        "gate_8: %s exposes router" % KEY,
        condition=False,
        error_class="missing_router_symbol",
        expected="module defines `router`",
        actual="router.py exposes no router",
    )

    reason = gate._files_this_run_bad.get(KEY)
    assert reason is not None, (
        "no reason recorded for a file whose check failed -- the retry "
        "ledger will fall back to the cohort label"
    )
    assert not COHORT_LABEL_RE.match(reason), (
        "reason is a cohort label, not a diagnosis: %r" % reason
    )
    assert "missing_router_symbol" in reason, (
        "the error_class Gate.check() already had was dropped: %r" % reason
    )


def test_first_reason_wins_not_the_last():
    """A file failing several checks keeps the FIRST diagnosis; a later
    check must not overwrite it, or the ledger reports a downstream
    symptom instead of the cause."""
    gate = _gate()
    gate._current_key = KEY

    gate.check("first", condition=False, error_class="syntax_error",
               expected="parses", actual="SyntaxError: line 3")
    gate.check("second", condition=False, error_class="import_failed",
               expected="imports", actual="ModuleNotFoundError")

    reason = gate._files_this_run_bad.get(KEY)
    assert "syntax_error" in reason, reason
    assert "import_failed" not in reason, reason


def test_passing_check_records_nothing():
    """Idempotence/no-op pole: a passing check must not create a ledger
    entry. Without this, the fix would quarantine healthy files."""
    gate = _gate()
    gate._current_key = KEY

    gate.check("ok", condition=True, error_class="assertion_failed")

    assert gate._files_this_run_bad == {}, gate._files_this_run_bad


def test_cohort_label_still_used_when_no_check_ran():
    """A file can be counted failed by the caller's failure-delta without
    any check naming it (e.g. a failure recorded under another key). The
    cohort-label fallback in _cohort_bump must survive as the backstop, so
    nothing regresses to an empty reason."""
    gate = _gate()
    gate._cohort_bump("cohort_1_n2", True, KEY)

    assert gate._files_this_run_bad.get(KEY) == "failed in cohort_1_n2"
