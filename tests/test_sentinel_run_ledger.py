"""Tests for tools/sentinel_run_ledger.py.

Every assertion here was seen RED against a deliberately wrong implementation
before it was trusted -- an assertion never seen red is not evidence, and one
that only catches a missing attribute is barely one.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sentinel_run_ledger as srl  # noqa: E402


def write_state(tmp_path: Path, last_check: str, receipts=None) -> Path:
    state = {"last_check_utc": last_check}
    if receipts is not None:
        state["run_receipts"] = receipts
    path = tmp_path / "prod_deploy_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def write_verdict(evidence_dir: Path, stamp: str, checked_utc: str | None) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    blob = {"verdict": "PASS", "head_sha": "7fc39201"}
    if checked_utc is not None:
        blob["checked_utc"] = checked_utc
    path = evidence_dir / f"verdict_7fc39201_{stamp}.json"
    path.write_text(json.dumps(blob), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the live defect this tool exists for
# --------------------------------------------------------------------------


def test_orphan_evidence_is_a_gap(tmp_path):
    """The 2026-07-29 case: gates ran at 07:51Z, state stopped at 05:01Z."""
    state = write_state(tmp_path, "2026-07-29T05:01:00Z")
    ev = tmp_path / "_deploy_evidence"
    write_verdict(ev, "20260729T075153Z", "2026-07-29T07:51:53Z")

    rc = srl.main(
        [
            "--state", str(state),
            "--evidence-dir", str(ev),
            "--now", "2026-07-29T10:49:00Z",
            "--window-hours", "6",
        ]
    )
    assert rc == srl.EXIT_GAP

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        6,
        25,
    )
    assert len(report["orphan_evidence"]) == 1
    assert report["orphan_evidence"][0]["checked_utc"] == "2026-07-29T07:51:53Z"
    assert report["clean"] is False


def test_a_receipt_redeems_the_orphan(tmp_path):
    """A run that recorded a receipt at its start is accounted for even if
    its state write never landed -- that is the whole point of the receipt."""
    state = write_state(
        tmp_path, "2026-07-29T05:01:00Z", receipts=["2026-07-29T07:47:00Z"]
    )
    ev = tmp_path / "_deploy_evidence"
    write_verdict(ev, "20260729T075153Z", "2026-07-29T07:51:53Z")

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        6,
        25,
    )
    assert report["orphan_evidence"] == []
    assert report["missed_slots"] == []
    assert report["clean"] is True


def test_missed_slot_with_no_trace_at_all(tmp_path):
    """A slot AT OR AFTER receipts began, with no trace, is a MISSED SLOT."""
    state = write_state(
        tmp_path, "2026-07-29T01:15:10Z", receipts=["2026-07-29T01:15:10Z"]
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:17:00Z"),
        9,
        25,
    )
    assert report["missed_slots"] == ["2026-07-29T04:15:00Z", "2026-07-29T07:15:00Z"]
    assert report["clean"] is False


def test_slots_predating_receipts_are_advisory_not_missed(tmp_path):
    """Only one last_check_utc survives, so an old slot's silence proves nothing.

    Absence of a trace is not evidence of absence until receipts exist to make
    presence recordable. Those slots must NOT turn the verdict red.
    """
    state = write_state(
        tmp_path, "2026-07-29T10:15:05Z", receipts=["2026-07-29T10:15:05Z"]
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        12,
        25,
    )
    assert report["missed_slots"] == []
    assert report["unattested_slots"] == [
        "2026-07-29T01:15:00Z",
        "2026-07-29T04:15:00Z",
        "2026-07-29T07:15:00Z",
    ]
    assert report["clean"] is True


def test_no_receipts_at_all_means_nothing_is_missed_yet(tmp_path):
    state = write_state(tmp_path, "2026-07-29T10:47:05Z")
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        12,
        25,
    )
    assert report["missed_slots"] == []
    assert len(report["unattested_slots"]) == 3
    assert report["attested_from"] is None
    assert report["clean"] is True


def test_verdict_latest_pointer_is_not_counted_as_a_run(tmp_path):
    """verdict_latest.json mirrors the newest artifact; counting it would
    invent a second run at the same instant."""
    ev = tmp_path / "_deploy_evidence"
    write_verdict(ev, "20260729T105125Z", "2026-07-29T10:51:25Z")
    (ev / "verdict_latest.json").write_text(
        json.dumps({"checked_utc": "2026-07-29T10:51:25Z"}), encoding="utf-8"
    )
    collected = srl.collect_evidence(ev)
    assert len(collected) == 1
    assert collected[0]["path"].endswith("verdict_7fc39201_20260729T105125Z.json")


def test_slot_still_in_flight_is_not_missed(tmp_path):
    """The slot that fired four minutes ago is THIS run; it is not yet owed a trace."""
    state = write_state(tmp_path, "2026-07-29T07:47:30Z")
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:51:00Z"),
        4,
        25,
    )
    assert report["missed_slots"] == []


# --------------------------------------------------------------------------
# FU-025: never mtime
# --------------------------------------------------------------------------


def test_timestamp_comes_from_content_not_mtime(tmp_path):
    """An artifact touched to 'now' is still dated by its checked_utc.

    mtimes do not track run age on this box, and they agree with the truth
    often enough to hide that they are the wrong method.
    """
    state = write_state(tmp_path, "2026-07-29T10:00:00Z")
    ev = tmp_path / "_deploy_evidence"
    path = write_verdict(ev, "20260728T105404Z", "2026-07-28T10:54:04Z")
    now_epoch = time.time()
    os.utime(path, (now_epoch, now_epoch))  # mtime says "just now"

    collected = srl.collect_evidence(ev)
    assert len(collected) == 1
    assert collected[0]["checked_utc"] == srl.parse_iso("2026-07-28T10:54:04Z")
    assert collected[0]["source"] == "checked_utc"
    # and it is therefore OLDER than last_check -- not an orphan
    report = srl.reconcile(
        srl.read_state(state),
        collected,
        srl.parse_iso("2026-07-29T10:49:00Z"),
        1,
        25,
    )
    assert report["orphan_evidence"] == []


def test_name_ts_is_the_only_fallback(tmp_path):
    """No checked_utc -> fall back to the NAME timestamp, still never mtime."""
    ev = tmp_path / "_deploy_evidence"
    write_verdict(ev, "20260729T075153Z", None)
    collected = srl.collect_evidence(ev)
    assert collected[0]["source"] == "name_ts"
    assert collected[0]["checked_utc"] == srl.parse_iso("2026-07-29T07:51:53Z")


def test_undatable_evidence_is_never_silently_clean(tmp_path):
    """Neither content nor name can date it: say so, do not guess."""
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()
    (ev / "verdict_7fc39201_nodate.json").write_text("{}", encoding="utf-8")
    state = write_state(tmp_path, "2026-07-29T10:47:05Z")

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        1,
        25,
    )
    assert report["undatable_evidence"]
    assert report["clean"] is False


# --------------------------------------------------------------------------
# a probe that cannot evaluate is not a green
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ["{ not json", json.dumps(["a", "list"]), json.dumps({"no": "last_check"})]
)
def test_unusable_state_is_error_never_clean(tmp_path, body):
    state = tmp_path / "prod_deploy_state.json"
    state.write_text(body, encoding="utf-8")
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()
    rc = srl.main(["--state", str(state), "--evidence-dir", str(ev)])
    assert rc == srl.EXIT_ERROR


def test_missing_evidence_dir_is_error_never_clean(tmp_path):
    state = write_state(tmp_path, "2026-07-29T10:47:05Z")
    rc = srl.main(["--state", str(state), "--evidence-dir", str(tmp_path / "nope")])
    assert rc == srl.EXIT_ERROR


def test_unparseable_receipt_is_error_never_clean(tmp_path):
    state = write_state(tmp_path, "2026-07-29T10:47:05Z", receipts=["not-a-time"])
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()
    rc = srl.main(
        ["--state", str(state), "--evidence-dir", str(ev), "--now", "2026-07-29T10:49:00Z"]
    )
    assert rc == srl.EXIT_ERROR


# --------------------------------------------------------------------------
# receipts
# --------------------------------------------------------------------------


def test_record_receipt_is_idempotent_and_preserves_state(tmp_path):
    state = write_state(tmp_path, "2026-07-29T05:01:00Z")
    original = json.loads(state.read_text(encoding="utf-8"))
    now = srl.parse_iso("2026-07-29T10:48:00Z")

    first = srl.record_receipt(state, now)
    second = srl.record_receipt(state, now)

    assert first["added"] is True
    assert second["added"] is False
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["run_receipts"] == ["2026-07-29T10:48:00Z"]
    assert after["last_check_utc"] == original["last_check_utc"]


def test_receipts_are_bounded(tmp_path):
    receipts = [
        srl.fmt(srl.parse_iso("2026-01-01T00:00:00Z") + timedelta(hours=3 * i))
        for i in range(200)
    ]
    state = write_state(tmp_path, "2026-07-29T05:01:00Z", receipts=receipts)
    result = srl.record_receipt(state, srl.parse_iso("2026-07-29T10:48:00Z"))
    assert result["count"] == 64


def test_expected_slots_are_every_three_hours_on_the_15(tmp_path):
    slots = srl.expected_slots(srl.parse_iso("2026-07-29T10:17:00Z"), 12)
    assert [s.strftime("%H:%M") for s in slots] == ["01:15", "04:15", "07:15", "10:15"]
    assert all(s.tzinfo == timezone.utc for s in slots)


def test_zero_window_is_error(tmp_path):
    with pytest.raises(srl.LedgerError):
        srl.expected_slots(datetime.now(timezone.utc), 0)


def test_a_run_that_fired_off_phase_still_attests_its_slot(tmp_path):
    """THE LIVE CASE, 2026-07-30 -- and note WHY it was a false alarm.

    The tool reported `2026-07-30T16:47:00Z` MISSED. There was never a 16:47
    slot: the task had been rescheduled that morning to `15 */3 * * *` local,
    so the real slot was 16:15 and the run at 16:17:01 was two minutes LATE,
    not thirty minutes early. The stale SLOT_MINUTE was the root cause; the
    reported "missed slot" was an artefact of a grid that no longer existed.

    This test pins the robustness half of the fix: even with the receipts
    sitting off-phase from the grid -- which is what the whole 24h window looks
    like across a reschedule, half the receipts on the old phase and half on the
    new -- nearest-slot attestation still covers every slot. Nine receipts, eight
    slots, nothing missed."""
    state = write_state(
        tmp_path,
        "2026-07-30T19:36:53Z",
        receipts=[
            "2026-07-30T07:48:58Z",
            "2026-07-30T10:49:04Z",
            "2026-07-30T13:49:03Z",
            "2026-07-30T16:17:01Z",
            "2026-07-30T19:22:02Z",
        ],
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-30T19:39:26Z"),
        12,
        25,
    )
    assert report["missed_slots"] == []
    assert report["clean"] is True


def test_negative_control_a_genuinely_skipped_slot_is_still_missed(tmp_path):
    """The control for the test above -- without it, the fix is indistinguishable
    from switching the check off. SAME shape, SAME slot, SAME window: only the
    16:47 run is removed. If this ever goes green, nearest-slot attestation has
    stopped being a measurement and has become an agreement."""
    state = write_state(
        tmp_path,
        "2026-07-30T19:36:53Z",
        receipts=[
            "2026-07-30T07:48:58Z",
            "2026-07-30T10:49:04Z",
            "2026-07-30T13:49:03Z",
            "2026-07-30T19:22:02Z",
        ],
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-30T19:39:26Z"),
        12,
        25,
    )
    assert report["missed_slots"] == ["2026-07-30T16:15:00Z"]
    assert report["clean"] is False


def test_a_run_more_than_half_a_cadence_off_does_not_attest(tmp_path):
    """The boundary is half a cadence, and it is a real boundary. A run 100
    minutes from the slot is nearer to the NEXT slot than to this one, so it
    attests that one and leaves this one missed -- rather than attesting both,
    which would let one run cover two slots."""
    state = write_state(
        tmp_path,
        "2026-07-30T17:55:00Z",
        receipts=["2026-07-30T13:15:00Z", "2026-07-30T17:55:00Z"],
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-30T20:00:00Z"),
        9,
        25,
    )
    # 17:55 is 100 min after 16:15 and 80 min before 19:15 -> it attests 19:15.
    assert "2026-07-30T16:15:00Z" in report["missed_slots"]


def test_grid_is_the_local_cron_converted():
    """SLOT_UTC_HHMM must BE the local cron converted -- derived here, not retyped.

    FU-210 hand-wrote the UTC grid from the assumption that the scheduler
    evaluates cron in UTC. It does not; it evaluates in LOCAL time. That put
    three of four daily slots at instants no run can occur, which would have
    reported MISSED (an email condition) forever. The bug was reachable only
    because the constant was typed rather than computed, so this test computes
    it. If SLOT_LOCAL_HHMM, SLOT_TZ or the UTC offset ever move -- including at
    the 2026-11-01 DST change -- this fails loudly instead of drifting.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(srl.SLOT_TZ)
    # A date inside US EDT, i.e. the offset the committed grid was written for.
    day = datetime(2026, 7, 31, tzinfo=tz)
    derived = tuple(
        sorted(
            (
                datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
                .astimezone(timezone.utc)
                .hour,
                mm,
            )
            for hh, mm in srl.SLOT_LOCAL_HHMM
        )
    )
    assert derived == srl.SLOT_UTC_HHMM, (
        f"grid {srl.SLOT_UTC_HHMM} is not {srl.SLOT_LOCAL_HHMM} in {srl.SLOT_TZ}; "
        f"derived {derived}"
    )
    # Four slots a day after the FU-207 cadence cut, not eight.
    assert len(srl.SLOT_UTC_HHMM) == 4


def test_a_utc_read_of_the_cron_is_refused():
    """NEGATIVE CONTROL: the exact grid FU-210 committed must FAIL the derivation.

    Without this, the test above passes trivially against any self-consistent
    pair and proves nothing about the bug it exists to prevent. The UTC reading
    differs from the local one at three of four slots; only 00:45 coincides,
    which is precisely why FU-210's single spot-check could not see it.
    """
    utc_reading = ((0, 45), (6, 45), (15, 45), (20, 45))
    assert utc_reading != srl.SLOT_UTC_HHMM
    coinciding = set(utc_reading) & set(srl.SLOT_UTC_HHMM)
    assert coinciding == {(0, 45)}, (
        "exactly one slot may coincide between the UTC and local readings -- if "
        "more do, the two hypotheses are no longer distinguishable here"
    )


def test_the_legacy_slot_grid_matches_the_pre_cut_cron():
    """The pre-cut grid still has to be right, or FU-207's five missed slots
    stop being visible in any 24h window that straddles the cut."""
    assert srl.LEGACY_SLOT_UTC_HHMM == tuple((h, 15) for h in range(1, 24, 3))

    now = srl.parse_iso("2026-07-30T19:44:45Z")
    slots = [srl.fmt(s) for s in srl.expected_slots(now, 12)]
    # local `15 */3 * * *` at UTC-4 -> 01:15Z, 04:15Z, 07:15Z, ...
    assert "2026-07-30T16:15:00Z" in slots
    assert "2026-07-30T19:15:00Z" in slots
    assert "2026-07-30T13:15:00Z" in slots


# --------------------------------------------------------------------------
# FU-213: the grid is FETCHED from the scheduler mirror, not typed.
#
# The three prior fixes (FU-205, FU-210, FU-211) each retyped the literal
# correctly and left the NEXT drift undetectable. These tests assert the thing
# that actually changed: a mirror that disagrees MOVES the grid, and says so.
# --------------------------------------------------------------------------
import json as _json

import tools.sentinel_run_ledger as srl
from tools import scheduler_mirror_read as smr


def _mirror(tmp_path, local_slots, generated=None, tz="America/New_York"):
    from datetime import datetime, timezone
    generated = generated or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    p = tmp_path / "scheduler_mirror.json"
    p.write_text(_json.dumps({
        "schema": 1, "generated_at": generated, "tz": tz,
        "tasks": {"prod-drift-sentinel": {
            "cronExpression": "45 0,6,15,20 * * *",
            "enabled": True,
            "local_slots": [list(s) for s in local_slots],
        }},
    }), encoding="utf-8")
    return str(p)


def test_mirror_agreeing_with_the_literal_reproduces_todays_grid(tmp_path, monkeypatch):
    """The live cron, converted, must equal the grid FU-211 hand-derived.

    An independent derivation landing on the same four instants is the check
    FU-210 and FU-211 each lacked.
    """
    monkeypatch.setattr(smr, "MIRROR_PATH",
                        _mirror(tmp_path, ((0, 45), (6, 45), (15, 45), (20, 45))))
    from datetime import date
    got = srl.resolve_post_cut_grid(date(2026, 7, 31))
    assert got == ((0, 45), (4, 45), (10, 45), (19, 45))
    assert got == tuple(srl.SLOT_UTC_HHMM)
    assert "mirror" in srl.GRID_BASIS


def test_a_disagreeing_mirror_MOVES_the_grid_and_announces_it(tmp_path, monkeypatch):
    """NEGATIVE CONTROL. If this passes with the literal still in force, the
    mirror is decorative and FU-213 changed nothing."""
    monkeypatch.setattr(smr, "MIRROR_PATH",
                        _mirror(tmp_path, ((2, 30), (14, 30))))
    from datetime import date
    got = srl.resolve_post_cut_grid(date(2026, 7, 31))
    assert got == ((6, 30), (18, 30))          # 02:30/14:30 local -> UTC-4
    assert got != tuple(srl.SLOT_UTC_HHMM)     # the literal did NOT win
    assert "DISAGREES" in srl.GRID_BASIS       # and it was not silent


def test_a_missing_mirror_falls_back_to_the_literal_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(smr, "MIRROR_PATH", str(tmp_path / "nope.json"))
    got = srl.resolve_post_cut_grid()
    assert got == tuple(srl.SLOT_UTC_HHMM)
    assert "literal" in srl.GRID_BASIS and "absent" in srl.GRID_BASIS


def test_a_stale_mirror_is_refused_rather_than_trusted(tmp_path, monkeypatch):
    """A mirror nobody has refreshed is not evidence about today's schedule."""
    monkeypatch.setattr(smr, "MIRROR_PATH",
                        _mirror(tmp_path, ((2, 30),), generated="2026-01-01T00:00:00Z"))
    got = srl.resolve_post_cut_grid()
    assert got == tuple(srl.SLOT_UTC_HHMM)     # literal, not the stale mirror
    assert "STALE" in srl.GRID_BASIS


def test_the_grid_survives_the_dst_change(tmp_path, monkeypatch):
    """2026-11-01: the same local cron is an hour later in UTC. A literal
    cannot notice; a date-aware conversion must."""
    monkeypatch.setattr(smr, "MIRROR_PATH",
                        _mirror(tmp_path, ((0, 45), (6, 45), (15, 45), (20, 45))))
    from datetime import date
    summer = srl.resolve_post_cut_grid(date(2026, 7, 31))
    winter = srl.resolve_post_cut_grid(date(2026, 12, 1))
    assert summer == ((0, 45), (4, 45), (10, 45), (19, 45))   # UTC-4
    assert winter == ((1, 45), (5, 45), (11, 45), (20, 45))   # UTC-5
    assert summer != winter


def test_unattributed_is_unknown_not_another_lane(tmp_path):
    """`unattributed` is the stamper's word for "I could not tell", so it must
    land in the SAME bucket as an absent field -- not in `foreign`.

    Measured 2026-08-07T19:49:42Z: prod-drift ran ops/host/verify_candidate.ps1
    from the SHARED checkout, so $PSScriptRoot had no `\\_lanes\\<name>` component
    and $env:ZO_LANE was unset. The script stamped "unattributed"; the ledger then
    reported prod-drift's OWN verdict as "another lane's dry-run" and EXCLUDED it
    from the orphan test -- the one reading that makes the guard go quiet instead
    of red. lane_of()'s own docstring already says unknown is not zero (R6);
    absence obeyed it and the sentinel VALUE did not.

    Two-point standard: the middle assert is the bug (it failed before the fix),
    the last assert is the positive control (a genuinely foreign lane must STILL
    be flagged, or the fix is a rubber stamp).
    """
    import json

    def stamped(name, lane):
        path = tmp_path / name
        path.write_text(json.dumps({"verdict": "PASS", "produced_by_lane": lane}),
                        encoding="utf-8")
        return path

    mine    = stamped("verdict_aaaaaaaa_20260807T190000Z.json", "prod-drift")
    unknown = stamped("verdict_bbbbbbbb_20260807T190100Z.json", "unattributed")
    absent  = tmp_path / "verdict_dddddddd_20260807T190300Z.json"
    absent.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    other   = stamped("verdict_cccccccc_20260807T190200Z.json", "some-other-lane")

    assert srl.lane_of(mine) == srl.THIS_LANE
    assert srl.lane_of(absent) is None
    assert srl.lane_of(unknown) is None, (
        "unattributed must read as UNKNOWN, exactly like an absent field")
    assert srl.lane_of(other) == "some-other-lane", (
        "positive control: a real sibling stamp must still be foreign")
