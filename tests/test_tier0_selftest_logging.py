"""FU-031 + FU-159: source-level guards on the builder self-test verdict branches.

HISTORY
-------
FU-031 (original): the degradation branch logged a CONSTANT string and discarded
`combined`, so 143 distinct failures collapsed into one indistinguishable bucket and
the diagnosis had to be reconstructed by hand-running each module. Fixed by
appending `combined.strip()[-400:]`.

FU-159 (this change): the branch CONDITION was also wrong. It matched on the
exception NAME (`"ModuleNotFoundError" in combined or "ImportError" in combined`),
which cannot separate "the harness could not run" from "the module is broken", so it
waived both. Measured post-#2177 (n=89): all 39 degradations were real module
defects; `No module named 'app.db'` had already gone 295 -> 0 across that merge. The
branch is now a three-state verdict (`tools/selftest_verdict`): RED blocks, UNKNOWN
degrades, and the two carry DISTINCT log strings.

These remain source-level guards: goose_runner.py does module-level filesystem setup
and heavy imports, so it cannot be imported in a unit test. The behaviour of the
verdict function itself is covered by tests/test_selftest_verdict.py.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO / "goose_runner.py").read_text(encoding="utf-8", errors="ignore")


def _log_stmt(marker: str) -> str:
    """The whole log(...) call, which may span continuation lines."""
    i = SRC.find(marker)
    assert i != -1, f"self-test {marker!r} log statement not found"
    start = SRC.rfind("log(", 0, i)
    assert start != -1, f"no log( call around {marker!r}"
    # walk to the matching close paren so continuations are included
    depth, j = 0, start
    while j < len(SRC):
        if SRC[j] == "(":
            depth += 1
        elif SRC[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SRC[start:j + 1]


def test_unknown_branch_logs_the_real_error_not_a_constant():
    """FU-031's durable property, carried forward onto the renamed branch."""
    assert "combined" in _log_stmt("self-test UNKNOWN"), (
        "the UNKNOWN/degrade branch must log `combined` (the real error), "
        "not a constant string -- see FU-031")


def test_unknown_branch_is_still_non_blocking():
    """An unevaluable probe must never block, or the factory halts on env noise."""
    idx = SRC.index("self-test UNKNOWN")
    assert "return True" in SRC[idx: idx + 500], (
        "UNKNOWN must remain non-blocking (return True)")


def test_red_branch_blocks_and_carries_detail():
    idx = SRC.index("self-test RED")
    tail = SRC[idx: idx + 500]
    assert "return False" in tail, "RED must block completion (return False)"
    assert "combined" in _log_stmt("self-test RED")


def test_the_two_meanings_have_distinct_log_strings():
    """FU-159, the core regression guard.

    One string for two meanings is why 39 real defects read as one environment
    problem for nine days. If these ever collapse back into a shared string, the
    bucket becomes unreadable again even if the control flow is correct.
    """
    assert "self-test RED" in SRC and "self-test UNKNOWN" in SRC
    assert "import/env failure" not in SRC, (
        "the old conflated string is back -- it named a cause the classifier "
        "could not actually determine")


def test_branch_condition_is_not_a_substring_match_on_exception_names():
    """The specific defect: classifying by exception NAME rather than by meaning."""
    assert '"ModuleNotFoundError" in combined' not in SRC, (
        "substring match on the exception name cannot distinguish a harness "
        "failure from a module defect -- use tools.selftest_verdict")
    assert "classify_selftest" in SRC, "the verdict must come from the shared classifier"
