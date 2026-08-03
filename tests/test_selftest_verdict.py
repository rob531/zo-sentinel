"""Negative controls for the three-state self-test verdict (FU-031 / FU-159).

Every case below is taken from a REAL line in the live goose_runner.log for the
window after #2177 (2026-07-28T12:19:15Z onward, n=89). The point of this suite is
not that the classifier returns something -- it is that each branch has been
OBSERVED going RED and UNKNOWN on purpose. An assertion never seen red is an
untested branch.
"""

import pytest

from tools.selftest_verdict import (PASS, RED, UNKNOWN, blocks_completion,
                                    classify_selftest)


# --------------------------------------------------------------------------
# RED -- the module is wrong. These were ALL waived as Tier-0 before this change.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("combined,why", [
    ("ImportError: cannot import name 'Orgs' from 'app.models' (/x/app/models.py)",
     "plural of a real model; 6 occurrences, the single largest family"),
    ("ImportError: cannot import name 'mesh_events' from 'app.models' (/x/app/models.py)",
     "snake_case TABLE name imported as a model class"),
    ("ImportError: cannot import name 'mcp_server_registry' from 'app.models'",
     "snake_case form of McpServerRegistry"),
    ("ImportError: cannot import name 'MeshMemory' from 'app.models'",
     "no such model exists at all"),
    ("ImportError: cannot import name 'StaticPool' from 'app.db'",
     "a SQLAlchemy name imported from a first-party module"),
    ("ImportError: cannot import name 'VulnerabilityLink' from 'app.models'",
     "invented long-form of VulnLink"),
    ("ImportError: attempted relative import with no known parent package",
     "single-file module using a relative import; 8 occurrences"),
    ("ModuleNotFoundError: No module named 'app.dependency_overrides'",
     "app/ is on disk but app/dependency_overrides.py is NOT -- invented import"),
])
def test_module_defects_are_RED_and_block(combined, why):
    verdict, reason = classify_selftest(1, combined)
    assert verdict == RED, f"{why}: got {verdict} ({reason})"
    assert blocks_completion(verdict), "a module defect must block completion"
    # Assert the REASON, not just the verdict. Mutation testing showed that
    # asserting the verdict alone is blind: reverting the classifier still
    # returns RED via the generic `rc != 0` fallback, so the suite passed on a
    # classifier that had stopped classifying. The diagnosis is the deliverable.
    assert reason != "self-test ran and failed", (
        f"{why}: fell through to the generic fallback -- the specific branch that "
        f"names WHY the module is wrong did not fire")
    assert any(k in reason for k in
               ("does not exist", "relative import")), f"unhelpful reason: {reason}"


# --------------------------------------------------------------------------
# UNKNOWN -- the harness could not run. Must STILL degrade, or the factory halts.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("combined,why", [
    ("ModuleNotFoundError: No module named 'app'",
     "the whole first-party root is unresolvable -- sys.path/cwd, the class #2177 fixed"),
    ("ModuleNotFoundError: No module named 'app.db'",
     "295 occurrences BEFORE #2177, 0 after; must remain non-blocking"),
    ("ModuleNotFoundError: No module named 'services'",
     "first-party root missing"),
    ("ModuleNotFoundError: No module named 'httpx'",
     "third-party dependency absent from the environment"),
])
def test_harness_failures_are_UNKNOWN_and_do_not_block(combined, why):
    verdict, reason = classify_selftest(1, combined)
    assert verdict == UNKNOWN, f"{why}: got {verdict} ({reason})"
    assert not blocks_completion(verdict), "an unevaluable probe must never block"


# --------------------------------------------------------------------------
# The distinction itself -- the whole point of the change.
# --------------------------------------------------------------------------

def test_app_db_missing_vs_app_db_name_missing_differ():
    """Same module, same exception family, OPPOSITE verdicts.

    This single assertion is the change. The old substring classifier gave both
    of these the same answer (degrade), which is how 39 real defects merged.
    """
    harness, _ = classify_selftest(1, "ModuleNotFoundError: No module named 'app.db'")
    defect, _ = classify_selftest(1, "ImportError: cannot import name 'StaticPool' from 'app.db'")
    assert harness == UNKNOWN
    assert defect == RED
    assert harness != defect


def test_pass_requires_the_marker_on_stdout():
    assert classify_selftest(0, "PASS", stdout="PASS")[0] == PASS
    # rc 0 but silent -> not a pass. Absence of output is not evidence of success.
    assert classify_selftest(0, "", stdout="")[0] == RED
    # PASS printed to stderr only must not earn credit.
    assert classify_selftest(1, "PASS on stderr", stdout="")[0] == RED


def test_unrecognised_shape_is_UNKNOWN_not_RED():
    """We never block on a shape we have not classified (R6: unknown is not a value)."""
    verdict, _ = classify_selftest(1, "ModuleNotFoundError: No module named 'numpy'")
    assert verdict == UNKNOWN


def test_non_import_failure_still_blocks_as_before():
    """Unchanged behaviour: a self-test that RUNS and fails already blocked."""
    verdict, _ = classify_selftest(1, "AssertionError: expected 3 got 4")
    assert verdict == RED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
