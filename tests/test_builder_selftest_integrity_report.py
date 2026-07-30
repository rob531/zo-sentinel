"""FU-196: the FU-031 probe must speak the vocabulary the emitter actually emits.

WHY THIS FILE EXISTS
--------------------
`tools/builder_selftest_integrity_report.py` grepped for `self-test FAILED` and
`import/env failure -- degrading to Tier-0`. `goose_runner.py:1057-1066` has emitted
the three-state contract (`self-test PASS` / `self-test RED (...)` / `self-test
UNKNOWN (...)`) since FU-159, plus a fourth `could not run (...) -- Tier-0 only`
shape at line 1045. **Neither grepped string existed in the live log.** Census of
`/home/workspace/logs/goose_runner.log` on 2026-07-30 (BASIS: the single log the
LIVE runner writes to, resolved from `/proc/<pid>/fd/1`): `self-test RED` 448 ·
`self-test FAILED` 0 · `degrading to Tier-0` 0 · `self-test PASS` 25.

A RED event matched no branch of `analyze()`, so it fell out of BOTH the numerator
and the denominator (`ran = pass + failed_blocking`). The probe published
`tier0-degraded: 0 / DEGRADATION RATE: 0% / executed pass-rate: 100%` over 448
blocking failures. That is HARNESS_DOCTRINE R3 in its purest form: a bucket that
went to zero because the check stopped running, published as a bucket that went to
zero because the problem was fixed.

The tests below are ordered so the NEGATIVE CONTROL comes first: `test_negative_
control_*` reconstructs the pre-fix regexes inline and proves they classify the real
log lines as nothing at all. Without it, every other assertion here is one that has
never been observed to fail (R4).
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import builder_selftest_integrity_report as probe  # noqa: E402

# --- verbatim lines from /home/workspace/logs/goose_runner.log, 2026-07-29/30 ---
RED_1 = (
    "[2026-07-29T08:41:43.311086+00:00] [selftest] build_vulnerability_associations_contract: "
    "self-test RED (self-test ran and failed) -- blocking completion :: "
    "vuln_advisories.affected_ranges, vuln_advisories.aliases\n"
)
RED_2 = (
    "[2026-07-29T08:52:33.779561+00:00] [selftest] build_perspective_membership_router: "
    "self-test RED (self-test ran and failed) -- blocking completion :: "
    "Traceback (most recent call last):\n"
)
RED_2_CAUSE = "  ModuleNotFoundError: No module named 'app.db'\n"
PASS_1 = (
    "[2026-07-29T10:47:06.641539+00:00] [selftest] "
    "build_vulnerability_advisory_manager_contract: self-test PASS\n"
)
# goose_runner.py:1065-1066 -- the UNKNOWN half of the three-state contract
UNKNOWN_1 = (
    "[2026-07-29T11:00:00.000000+00:00] [selftest] build_thing_contract: "
    "self-test UNKNOWN (harness could not import the module) -- could not evaluate, "
    "degrading to Tier-0 (not blocking) :: ImportError: cannot import name 'Foo'\n"
)
# goose_runner.py:1045 -- the self-test harness itself never started
COULD_NOT_RUN_1 = (
    "[2026-07-29T11:05:00.000000+00:00] [selftest] build_other_contract: "
    "could not run (FileNotFoundError: no contract.py) -- Tier-0 only\n"
)
# legacy vocabulary, still present in archived logs (pre-FU-159)
LEGACY_FAILED = (
    "[2026-07-19T09:00:00.000000+00:00] [selftest] old_module: "
    "self-test FAILED -- blocking completion :: AttributeError: nope\n"
)
LEGACY_DEGRADE = (
    "[2026-07-19T09:01:00.000000+00:00] [selftest] old_module_two: "
    "import/env failure -- degrading to Tier-0 (not blocking) :: ImportError: x\n"
)

LIVE = [RED_1, RED_2, RED_2_CAUSE, PASS_1, UNKNOWN_1, COULD_NOT_RUN_1]


# --------------------------------------------------------------------------
# NEGATIVE CONTROL -- run first, on purpose (HARNESS_DOCTRINE R4).
# --------------------------------------------------------------------------
def test_negative_control_pre_fix_regexes_match_nothing_in_the_live_vocabulary():
    """The exact regexes shipped before this fix, applied to the real log lines.

    If this test ever starts failing it means someone reintroduced the old
    vocabulary, and the rest of this file would be asserting nothing.
    """
    old_failed = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test FAILED")
    old_degrade = re.compile(
        r"\[selftest\]\s+(\S+?):\s+import/env failure -- degrading to Tier-0")
    for line in LIVE:
        assert not old_failed.search(line), line
        assert not old_degrade.search(line), line


def test_negative_control_pre_fix_report_would_claim_zero_degradation():
    """Reproduce the published lie: 0 degraded, 100% pass, over real RED events."""
    saved_failed, saved_degrade = probe.FAILED, probe.DEGRADE
    probe.FAILED = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test FAILED")
    probe.DEGRADE = re.compile(
        r"\[selftest\]\s+(\S+?):\s+import/env failure -- degrading to Tier-0")
    try:
        rep = probe.analyze(LIVE)
    finally:
        probe.FAILED, probe.DEGRADE = saved_failed, saved_degrade
    assert rep["selftest_failed_blocking"] == 0
    assert rep["tier0_degraded"] == 0
    assert rep["degradation_rate"] == 0.0
    assert rep["executed_pass_rate"] == 1.0
    # and the drift detector is what makes that report readable as broken
    assert rep["unrecognised_selftest_lines"] >= 4
    assert rep["trustworthy"] is False


# --------------------------------------------------------------------------
# The fix.
# --------------------------------------------------------------------------
def test_red_is_counted_as_a_blocking_failure():
    rep = probe.analyze([RED_1, RED_2, RED_2_CAUSE, PASS_1])
    assert rep["selftest_failed_blocking"] == 2
    assert rep["selftest_pass"] == 1
    assert rep["executed"] == 3
    assert rep["executed_pass_rate"] == round(1 / 3, 3)


def test_unknown_is_degraded_never_a_pass():
    rep = probe.analyze([UNKNOWN_1, PASS_1])
    assert rep["tier0_degraded"] == 1
    assert rep["selftest_pass"] == 1
    assert rep["selftest_failed_blocking"] == 0
    # unknown != zero and unknown != pass: it inflates attempts, not the pass rate
    assert rep["attempts"] == 2
    assert rep["executed"] == 1
    assert rep["executed_pass_rate"] == 1.0
    assert rep["degradation_rate"] == 0.5


def test_could_not_run_is_degraded():
    rep = probe.analyze([COULD_NOT_RUN_1])
    assert rep["tier0_degraded"] == 1
    assert rep["executed"] == 0


def test_legacy_vocabulary_still_parses():
    """Historical logs must not silently become unparseable when we move on."""
    rep = probe.analyze([LEGACY_FAILED, LEGACY_DEGRADE])
    assert rep["selftest_failed_blocking"] == 1
    assert rep["tier0_degraded"] == 1
    assert rep["trustworthy"] is True


def test_live_vocabulary_is_fully_recognised():
    rep = probe.analyze(LIVE)
    assert rep["unrecognised_selftest_lines"] == 0, rep["unrecognised_samples"]
    assert rep["trustworthy"] is True
    assert rep["selftest_failed_blocking"] == 2
    assert rep["selftest_pass"] == 1
    assert rep["tier0_degraded"] == 2
    assert rep["attempts"] == 5
    assert rep["degradation_rate"] == 0.4


def test_a_future_vocabulary_drift_is_surfaced_not_swallowed():
    """The guard that makes a THIRD drift visible instead of silently zeroing."""
    future = ("[2026-08-01T00:00:00.000000+00:00] [selftest] some_module: "
              "self-test MAGENTA (a word nobody has invented yet) -- blocking\n")
    rep = probe.analyze([PASS_1, future])
    assert rep["unrecognised_selftest_lines"] == 1
    assert rep["trustworthy"] is False
    assert "MAGENTA" in rep["unrecognised_samples"][0]
    # and the drifted event is NOT quietly counted as a pass
    assert rep["selftest_pass"] == 1
    assert rep["executed"] == 1


def test_root_cause_bucketing_still_works_on_red():
    rep = probe.analyze([RED_2, RED_2_CAUSE])
    causes = [b["cause"] for b in rep["shared_cause_buckets"]]
    assert any("app.db" in c for c in causes), causes
