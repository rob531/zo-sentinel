"""Tests for tools/ops_audit_state.py -- the ops audit's spend memory.

The bugs under test are the ones that shipped a wrong number quietly:
history that was overwritten every run, and a delta that goes NEGATIVE
across a top-up.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import ops_audit_state as oas  # noqa: E402


@pytest.fixture()
def statefile(tmp_path):
    return tmp_path / "ops_audit_state.json"


def test_missing_file_yields_empty_state_not_an_exception(statefile):
    st = oas.load(statefile)
    assert st["entries"] == [] and st["credits"] == []
    assert oas.month_to_date(st)["spend_usd"] is None


def test_v1_single_object_is_migrated_not_discarded(statefile):
    statefile.write_text(json.dumps({"date": "2026-07-26", "balance": 18.784}))
    st = oas.load(statefile)
    assert st["migrated_from"] == 1
    assert st["entries"] == [{"date": "2026-07-26", "at": "", "balance": 18.784}]


def test_corrupt_file_is_healed_rather_than_fatal(statefile):
    statefile.write_text("{not json at all")
    st = oas.load(statefile)
    assert st["entries"] == [] and st["migrated_from"] == "unreadable"


def test_history_accumulates_and_same_day_upserts(statefile):
    oas.record(20.0, "2026-07-01", statefile)
    oas.record(18.0, "2026-07-02", statefile)
    oas.record(17.5, "2026-07-02", statefile)   # re-run of the same day
    st = oas.load(statefile)
    assert [e["date"] for e in st["entries"]] == ["2026-07-01", "2026-07-02"]
    assert st["entries"][-1]["balance"] == 17.5


def test_mtd_is_plain_delta_when_no_credits(statefile):
    oas.record(25.0, "2026-07-01", statefile)
    oas.record(17.14, "2026-07-27", statefile)
    mtd = oas.month_to_date(oas.load(statefile), "2026-07")
    assert mtd["spend_usd"] == pytest.approx(7.86)
    assert mtd["complete_month"] is True and mtd["basis_days"] == 26


def test_midmonth_topup_does_not_make_spend_negative(statefile):
    """THE BUG: balance rose over the month, but $25 was spent."""
    oas.record(2.0, "2026-07-01", statefile)
    oas.record_credit(25.0, "2026-07-17", credit_id=3148330, path=statefile)
    oas.record(17.14, "2026-07-27", statefile)
    mtd = oas.month_to_date(oas.load(statefile), "2026-07")
    naive = 2.0 - 17.14
    assert naive < 0                              # what the old math said
    assert mtd["spend_usd"] == pytest.approx(9.86)  # 2 + 25 - 17.14
    assert mtd["credits_added"] == 25.0


def test_credit_recording_is_idempotent_on_id(statefile):
    oas.record_credit(25.0, "2026-07-17", credit_id=3148330, path=statefile)
    oas.record_credit(25.0, "2026-07-17", credit_id=3148330, path=statefile)
    assert len(oas.load(statefile)["credits"]) == 1


def test_credit_before_first_entry_is_not_double_counted(statefile):
    """A top-up that predates the first sample is already IN that balance."""
    oas.record_credit(25.0, "2026-07-01", credit_id=1, path=statefile)
    oas.record(25.0, "2026-07-10", statefile)
    oas.record(20.0, "2026-07-20", statefile)
    mtd = oas.month_to_date(oas.load(statefile), "2026-07")
    assert mtd["spend_usd"] == pytest.approx(5.0)
    assert mtd["credits_added"] == 0.0


def test_mtd_declares_a_partial_basis(statefile):
    """History that starts mid-month must not masquerade as a full month."""
    oas.record(18.78, "2026-07-26", statefile)
    oas.record(17.14, "2026-07-27", statefile)
    mtd = oas.month_to_date(oas.load(statefile), "2026-07")
    assert mtd["complete_month"] is False
    assert mtd["basis_days"] == 1
    assert mtd["first_entry_date"] == "2026-07-26"


def test_cli_round_trip(statefile, capsys):
    oas.main(["record", "--balance", "17.14", "--date", "2026-07-27",
              "--path", str(statefile), "--month", "2026-07"])
    out = json.loads(capsys.readouterr().out)
    assert out["entries"] == 1 and out["mtd"]["current_balance"] == 17.14


def test_since_funding_is_exact_without_history(statefile):
    """One credit + one balance is enough -- no dense history required."""
    oas.record_credit(25.0, "2026-07-17", credit_id=3148330, path=statefile)
    oas.record(17.10, "2026-07-27", statefile)
    sf = oas.since_funding(oas.load(statefile))
    assert sf["spend_usd"] == pytest.approx(7.90)
    assert sf["credits_ever"] == 25.0 and sf["last_funded"] == "2026-07-17"


def test_since_funding_beats_a_thin_monthly_basis(statefile):
    """The month delta UNDERSTATES the burn when history starts late; the
    funding-based number does not. Both are reported for exactly this reason."""
    oas.record_credit(25.0, "2026-07-17", credit_id=1, path=statefile)
    oas.record(18.78, "2026-07-26", statefile)
    oas.record(17.10, "2026-07-27", statefile)
    st = oas.load(statefile)
    assert oas.month_to_date(st, "2026-07")["spend_usd"] == pytest.approx(1.68)
    assert oas.since_funding(st)["spend_usd"] == pytest.approx(7.90)


def test_since_funding_declines_to_guess_without_a_credit_event(statefile):
    oas.record(17.10, "2026-07-27", statefile)
    assert oas.since_funding(oas.load(statefile))["spend_usd"] is None



if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# --- FU-035: THE BUDGET LINE IS NOW CODE, NOT PROSE ------------------------
# A threshold that lives only in a SKILL's prose fires only if the reading
# agent applies it. These pin the verdict itself.


def _state_with(balance, credits=25.0):
    st = oas.empty_state()
    st["entries"] = [{"date": "2026-07-29", "at": "x", "balance": balance}]
    if credits is not None:
        st["credits"] = [{"date": "2026-07-17", "at": "x", "amount": credits,
                          "id": 1}]
    return st


def test_budget_green_below_the_red_line():
    b = oas.budget_status(_state_with(15.44))
    assert b["level"] == "GREEN"
    assert b["spend_usd"] == 9.56
    assert b["remaining_usd"] == 15.44


def test_budget_red_exactly_at_the_line_is_red_not_green():
    # ">=$20 spent" -- the boundary belongs to RED.
    b = oas.budget_status(_state_with(5.0))
    assert b["spend_usd"] == 20.0
    assert b["level"] == "RED"


def test_budget_red_above_the_line():
    assert oas.budget_status(_state_with(1.0))["level"] == "RED"


def test_no_credit_history_is_UNKNOWN_never_a_confident_green():
    # The FU-035 failure shape: absent history must not read as all-clear.
    b = oas.budget_status(_state_with(15.44, credits=None))
    assert b["level"] == "UNKNOWN"
    assert b["level"] != "GREEN"
    assert b["spend_usd"] is None


def test_budget_is_judged_on_since_funding_not_the_thin_mtd_delta():
    # Two same-month entries: the MTD delta sees $2, since_funding sees $20.
    st = oas.empty_state()
    st["entries"] = [{"date": "2026-07-28", "at": "x", "balance": 7.0},
                     {"date": "2026-07-29", "at": "x", "balance": 5.0}]
    st["credits"] = [{"date": "2026-07-17", "at": "x", "amount": 25.0, "id": 1}]
    assert oas.month_to_date(st, month="2026-07")["spend_usd"] == 2.0  # GREEN
    assert oas.budget_status(st)["level"] == "RED"          # the honest verdict


def test_show_actually_emits_the_budget_block(statefile, capsys):
    # An uncalled helper is a placebo -- assert the CLI surfaces the verdict.
    oas.record_credit(25.0, "2026-07-17", 1, path=statefile)
    oas.record(15.44, "2026-07-29", statefile)
    rc = oas.main(["show", "--path", str(statefile)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["budget"]["level"] == "GREEN"
    assert out["budget"]["red_at_usd"] == 20.0


# --- observation coverage (FU-207 class) -------------------------------------
# The audit is the only writer of entries[], so a missing date is a missed run.
# These pin a REPORT, not a gate: no exit code or verdict depends on them.

def _st(dates):
    return {"schema": 2,
            "entries": [{"date": d, "at": d + "T00:00:00+00:00",
                         "balance": 10.0} for d in dates],
            "credits": []}


def test_coverage_names_the_day_the_lane_did_not_run():
    c = oas.coverage(_st(["2026-07-01", "2026-07-03"]))
    assert c["missing_dates"] == ["2026-07-02"]
    assert c["complete"] is False
    assert c["observed_days"] == 2
    assert c["span_days"] == 3


def test_coverage_catches_a_gap_in_a_MONTH_SEAM():
    """THE CASE THE FIRST DRAFT COULD NOT SEE, pinned so it cannot regress.

    The live 2026-08-01 file: 07-26..07-30 then 08-01, missing 07-31. Scoped
    to July it is complete; scoped to August it is complete; the missed run
    is only visible when the scan spans months. A month-scoped detector
    reports CLEAN on the exact event it was built for.
    """
    real = ["2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29",
            "2026-07-30", "2026-08-01"]
    assert oas.coverage(_st(real), month="2026-07")["complete"] is True
    assert oas.coverage(_st(real), month="2026-08")["complete"] is True
    c = oas.coverage(_st(real))          # default scope = all history
    assert c["missing_dates"] == ["2026-07-31"]
    assert c["scope"] == "all"


def test_coverage_is_clean_when_every_day_was_observed():
    c = oas.coverage(_st(["2026-07-01", "2026-07-02", "2026-07-03"]))
    assert c["missing_dates"] == []
    assert c["complete"] is True
    assert c["observed_days"] == c["span_days"] == 3


def test_no_entries_is_unknown_coverage_not_complete():
    # R6: unknown != zero. An empty history must not report itself complete.
    c = oas.coverage(_st([]))
    assert c["complete"] is None
    assert c["observed_days"] == 0


def test_mtd_carries_observed_days_beside_its_calendar_basis():
    # The whole point: basis_days is a calendar span and can overstate how
    # much of the window was actually looked at.
    st = _st(["2026-07-01", "2026-07-03"])
    m = oas.month_to_date(st, month="2026-07")
    assert m["basis_days"] == 2          # calendar span, unchanged
    assert m["observed_days"] == 2       # but only 2 of the 3 days were seen
    assert m["missing_days"] == 1


def test_show_emits_coverage_and_does_not_scope_it_to_the_month(tmp_path,
                                                               capsys):
    """An uncalled helper is a placebo -- and a MIS-called one is worse.

    `--month` narrows the MTD read; passing it through to coverage would
    reintroduce the seam blindness above, so show must call coverage with
    no month even when the user asked for one.
    """
    p = tmp_path / "s.json"
    oas.save(_st(["2026-07-30", "2026-08-01"]), p)
    rc = oas.main(["show", "--month", "2026-08", "--path", str(p)])
    assert rc == 0                       # report, not a gate
    out = json.loads(capsys.readouterr().out)
    assert out["coverage"]["missing_dates"] == ["2026-07-31"]
    assert out["mtd"]["month"] == "2026-08"   # --month still honoured by mtd
