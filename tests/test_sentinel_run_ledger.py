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
        tmp_path, "2026-07-29T01:47:10Z", receipts=["2026-07-29T01:47:10Z"]
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-29T10:49:00Z"),
        9,
        25,
    )
    assert report["missed_slots"] == ["2026-07-29T04:47:00Z", "2026-07-29T07:47:00Z"]
    assert report["clean"] is False


def test_slots_predating_receipts_are_advisory_not_missed(tmp_path):
    """Only one last_check_utc survives, so an old slot's silence proves nothing.

    Absence of a trace is not evidence of absence until receipts exist to make
    presence recordable. Those slots must NOT turn the verdict red.
    """
    state = write_state(
        tmp_path, "2026-07-29T10:47:05Z", receipts=["2026-07-29T10:47:05Z"]
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
        "2026-07-29T01:47:00Z",
        "2026-07-29T04:47:00Z",
        "2026-07-29T07:47:00Z",
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


def test_expected_slots_are_every_three_hours_on_the_47(tmp_path):
    slots = srl.expected_slots(srl.parse_iso("2026-07-29T10:49:00Z"), 12)
    assert [s.strftime("%H:%M") for s in slots] == ["01:47", "04:47", "07:47", "10:47"]
    assert all(s.tzinfo == timezone.utc for s in slots)


def test_zero_window_is_error(tmp_path):
    with pytest.raises(srl.LedgerError):
        srl.expected_slots(datetime.now(timezone.utc), 0)


def test_a_run_that_fired_early_still_attests_its_slot(tmp_path):
    """THE LIVE CASE, 2026-07-30. The 16:47 run started at 16:17:01 -- thirty
    minutes early, five minutes outside the 25-minute tolerance -- and then ran
    for three hours. The old nominal-phase test called that slot MISSED, i.e.
    "cron came due and left no trace at all", while the trace sat in the
    receipts list it had just read. Nine receipts covered eight slots."""
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
    assert report["missed_slots"] == ["2026-07-30T16:47:00Z"]
    assert report["clean"] is False


def test_a_run_more_than_half_a_cadence_off_does_not_attest(tmp_path):
    """The boundary is half a cadence, and it is a real boundary. A run 100
    minutes from the slot is nearer to the NEXT slot than to this one, so it
    attests that one and leaves this one missed -- rather than attesting both,
    which would let one run cover two slots."""
    state = write_state(
        tmp_path,
        "2026-07-30T18:27:00Z",
        receipts=["2026-07-30T13:47:00Z", "2026-07-30T18:27:00Z"],
    )
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()

    report = srl.reconcile(
        srl.read_state(state),
        srl.collect_evidence(ev),
        srl.parse_iso("2026-07-30T20:30:00Z"),
        9,
        25,
    )
    # 18:27 is 100 min after 16:47 and 80 min before 19:47 -> it attests 19:47.
    assert "2026-07-30T16:47:00Z" in report["missed_slots"]
