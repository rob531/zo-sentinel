"""Negative control for the foreign-lane exclusion in sentinel_run_ledger.

R4: an assertion never seen RED is UNPROVEN, not passing. The exclusion added
2026-08-02 makes an alarm QUIETER, which is exactly the class of change that
must be shown to still fire. So all three cases are asserted here, and the
FIRST two are the ones that matter:

  1. UNSTAMPED artifact with no matching receipt  -> still ORPHAN  (old
     behaviour preserved; unknown is not "mine", R6)
  2. Artifact stamped with THIS lane, no receipt  -> still ORPHAN  (the
     exclusion cannot be used to launder our own missing record)
  3. Artifact stamped with ANOTHER lane           -> NOT orphan, listed as
     foreign  (the bug being fixed)

Every case runs against a throwaway evidence dir and a synthetic state file,
so the live ledger is never touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import sentinel_run_ledger as srl  # noqa: E402


LAST_CHECK = "2026-08-02T10:54:01Z"
CHECKED = "2026-08-02T18:15:18Z"      # after last_check, far from any receipt
RECEIPTS = ["2026-08-02T10:47:01Z", "2026-08-02T19:47:05Z"]
NOW = srl.parse_iso("2026-08-02T19:54:10Z")


def _fixture(tmp_path: Path, lane):
    """One dated verdict artifact, optionally stamped with a producing lane."""
    ev = tmp_path / "_deploy_evidence"
    ev.mkdir()
    blob = {
        "verdict": "PASS",
        "head_sha": "9d365abd03f1f9901c91f01d26e0fc4e142de36d",
        "gates_run": 8,
        "gates_skipped": 0,
        "failing": [],
        "checked_utc": CHECKED,
    }
    if lane is not None:
        blob["produced_by_lane"] = lane
    (ev / "verdict_9d365abd_20260802T181518Z.json").write_text(
        json.dumps(blob, indent=2), encoding="utf-8"
    )
    state = {"last_check_utc": LAST_CHECK, "run_receipts": list(RECEIPTS)}
    return srl.reconcile(
        state,
        srl.collect_evidence(ev),
        now=NOW,
        window_hours=24,
        tolerance_min=25,
    )


def test_unstamped_artifact_is_still_orphan(tmp_path):
    """The negative control. No stamp means UNKNOWN, and unknown is not mine."""
    report = _fixture(tmp_path, lane=None)
    assert len(report["orphan_evidence"]) == 1
    assert report["foreign_evidence"] == []


def test_own_lane_artifact_is_still_orphan(tmp_path):
    """An artifact WE produced with no receipt near it is our missing record."""
    report = _fixture(tmp_path, lane=srl.THIS_LANE)
    assert len(report["orphan_evidence"]) == 1
    assert report["foreign_evidence"] == []


def test_foreign_lane_artifact_is_excluded_and_reported(tmp_path):
    """The bug: a sibling's dry-run must not read as our missing record."""
    report = _fixture(tmp_path, lane="clerk-sync")
    assert report["orphan_evidence"] == []
    assert len(report["foreign_evidence"]) == 1
    assert report["foreign_evidence"][0]["lane"] == "clerk-sync"


def test_foreign_evidence_is_rendered_not_silently_dropped(tmp_path):
    """An exclusion nobody can see is indistinguishable from a dead check."""
    report = _fixture(tmp_path, lane="clerk-sync")
    text = srl.render(report)
    assert "foreign evidence" in text
    assert "clerk-sync" in text
