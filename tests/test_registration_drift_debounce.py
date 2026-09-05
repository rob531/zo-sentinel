"""Regression for #4706: two check instances sharing one state file counted
"cycle 1" and "cycle 2" four seconds apart during a go.sh boot and filed an
issue for a daemon that started 20s later. The debounce must be a clock, not a
counter. NEGATIVE CONTROL: the 4-second case must NOT earn an issue; the
positive case (same count, 10+ minutes old) must."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "tools" / "registration_drift_check.py"
spec = importlib.util.spec_from_file_location("registration_drift_check", SRC)
rdc = importlib.util.module_from_spec(spec)
sys.modules["registration_drift_check"] = rdc
spec.loader.exec_module(rdc)


def _since(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_two_samples_four_seconds_apart_do_not_earn_an_issue():
    # the exact shape #4706 was filed from
    rec = {"cycles": 2, "since": _since(4)}
    assert rdc.earns_issue(rec) is False


def test_two_samples_across_a_real_outage_do_earn_an_issue():
    rec = {"cycles": 2, "since": _since(rdc.ISSUE_AFTER_SECONDS + 5)}
    assert rdc.earns_issue(rec) is True


def test_one_sample_never_earns_even_if_old():
    rec = {"cycles": 1, "since": _since(99999)}
    assert rdc.earns_issue(rec) is False


def test_corrupt_since_delays_rather_than_forges():
    assert rdc.earns_issue({"cycles": 5, "since": "not-a-date"}) is False
    assert rdc.earns_issue({"cycles": 5}) is False


def test_threshold_fits_the_tick():
    # one process on the default tick reaches the clock on its 2nd sample;
    # the clock must not be so large that it needs a 3rd.
    assert rdc.ISSUE_AFTER_SECONDS < rdc.DEFAULT_INTERVAL
