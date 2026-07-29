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
              "--path", str(statefile)])
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


# --- healing must not be the step that destroys the evidence (2026-07-28) ---
#
# load() heals a bad state file so the audit can keep auditing. Before this
# guard, the heal was in place: the next save() overwrote the only copy. The
# scar was self-inflicted -- the daily audit hand-wrote {"history": [...]}
# over the real entries[]/credits[] -- and recovery depended on luck.


def _sidecars(statefile):
    return sorted(statefile.parent.glob(statefile.stem + ".*.bak.json"))


def test_healing_a_clobbered_file_quarantines_the_original(statefile):
    clobbered = json.dumps({"history": [{"date": "2026-07-28", "balance": 15.4}]})
    statefile.write_text(clobbered)

    st = oas.load(statefile)

    assert st["migrated_from"] == "malformed"
    assert st["entries"] == []
    saved = _sidecars(statefile)
    assert len(saved) == 1, "the original must survive the heal"
    assert json.loads(saved[0].read_text()) == json.loads(clobbered)
    assert "malformed" in saved[0].name


def test_healing_unreadable_json_also_quarantines(statefile):
    statefile.write_text("{not json at all")

    st = oas.load(statefile)

    assert st["migrated_from"] == "unreadable"
    saved = _sidecars(statefile)
    assert len(saved) == 1
    assert saved[0].read_text() == "{not json at all"
    assert "unreadable" in saved[0].name


def test_a_healthy_file_is_never_quarantined(statefile):
    # Otherwise every daily run would drop a sidecar next to a fine file.
    oas.record(15.4, "2026-07-28", statefile)
    oas.load(statefile)
    oas.record(15.1, "2026-07-29", statefile)
    assert _sidecars(statefile) == []


def test_a_v1_file_is_migrated_not_quarantined(statefile):
    # v1 is a KNOWN good shape with a real datapoint -- migrating it is not
    # healing past damage, so it must not be treated as corruption.
    statefile.write_text(json.dumps({"date": "2026-07-26", "balance": 18.784}))
    st = oas.load(statefile)
    assert st["migrated_from"] == 1
    assert _sidecars(statefile) == []


def test_quarantine_failure_never_stops_the_audit(statefile, monkeypatch):
    statefile.write_text(json.dumps({"history": []}))

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "write_bytes", boom)

    st = oas.load(statefile)  # must not raise

    assert st["migrated_from"] == "malformed"
    assert st["entries"] == []
