"""FU-031: the Tier-0 self-test degradation branch must log the REAL import/env
error, not a constant string.

Before this fix, goose_runner._selftest_gate matched ImportError/ModuleNotFoundError
and logged a constant "import/env failure -- degrading to Tier-0 (not blocking)"
while DISCARDING `combined` (the captured stdout+stderr). Because the string was
constant, 143 distinct failures collapsed into one indistinguishable bucket, and a
diagnosis (FU-031) had to be reconstructed by hand-running each module. The fix
appends `combined.strip()[-400:]` exactly as the sibling "self-test FAILED" branch
already does, turning the constant into bucketable data. It changes no control flow
(the branch still returns True / non-blocking).

This is a source-level guard: goose_runner.py performs module-level filesystem setup
(LOGS_DIR.mkdir) and heavy imports on import, so it cannot be imported in a unit
test. The guard asserts the durable property directly against the source: the Tier-0
branch must carry the error detail and must not regress to a constant string.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO / "goose_runner.py").read_text(encoding="utf-8", errors="ignore")


def _tier0_log_statement() -> str:
    # the single log(...) call on the Tier-0 degradation branch
    m = re.search(r'log\(f"\[selftest\] \{directive_id\}: import/env failure[^\n]*', SRC)
    assert m, "Tier-0 degradation log statement not found"
    return m.group(0)


def test_tier0_branch_logs_the_real_error_not_a_constant():
    stmt = _tier0_log_statement()
    # the captured stdout+stderr must be emitted, so failures are distinguishable
    assert "combined" in stmt, (
        "Tier-0 degradation must log `combined` (the real ImportError), "
        "not a constant string -- see FU-031"
    )


def test_tier0_branch_is_still_non_blocking():
    # the fix must not change control flow: the branch still degrades (returns True)
    idx = SRC.index("import/env failure -- degrading to Tier-0")
    tail = SRC[idx: idx + 400]
    assert "return True" in tail, "Tier-0 branch must remain non-blocking (return True)"


def test_failed_branch_still_carries_detail():
    # sibling regression guard: the blocking FAILED path already logged the detail
    assert 'self-test FAILED -- blocking completion' in SRC
    m = re.search(r'self-test FAILED -- blocking completion[^\n]*', SRC)
    assert "combined" in m.group(0)
